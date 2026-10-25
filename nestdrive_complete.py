import os
import io
import re
import sqlite3
import hashlib
import mimetypes
import zipfile
import xml.etree.ElementTree as ET
import json
from pathlib import Path, PurePosixPath
from functools import wraps
from flask import Flask, request, redirect, session, send_file, render_template_string, url_for, abort, flash
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import RequestEntityTooLarge

from auth_config import BASE, STORAGE, DATABASE, SECRET_KEY, PASSWORD_METHOD

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024**3


@app.after_request
def prevent_browser_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Existing uploads are preserved in STORAGE. New file bytes live in .objects,
# named by SHA-256, so identical content can share one physical object.
OBJECTS = STORAGE / ".objects"
LEGACY_TRASH = STORAGE / ".trash"
OBJECTS.mkdir(parents=True, exist_ok=True)
LEGACY_TRASH.mkdir(parents=True, exist_ok=True)

TYPES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"},
    "Videos": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".3gp"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                  ".txt", ".csv", ".rtf", ".odt", ".ods"},
}

CSS = """
:root{--bg:#f6f8fc;--card:#fff;--ink:#17233b;--muted:#8290a7;--line:#e6ebf3;--blue:#4361ee;--pale:#edf1ff;--shadow:0 5px 22px #17233b08}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px system-ui,-apple-system,sans-serif}
a{color:inherit;text-decoration:none}button,input{font:inherit}button{cursor:pointer}
.top{min-height:70px;position:sticky;top:0;z-index:4;background:#ffffffed;border-bottom:0;display:flex;align-items:center;gap:16px;padding:13px 22px;flex-wrap:wrap}
.brand{font-size:23px;font-weight:850;white-space:nowrap}.brand span{color:var(--blue)}
.search{flex:1;max-width:590px}.search input{width:100%;padding:12px;border:1px solid var(--line);border-radius:12px;background:#f7f8fc}
.btn{border:0;border-radius:10px;padding:11px 15px;font-weight:700;display:inline-block}.blue{background:var(--blue);color:white}.plain{background:white;border:1px solid var(--line)}
.shell{display:grid;grid-template-columns:220px minmax(0,1fr);min-height:calc(100vh - 70px)}aside{background:white;border-right:1px solid var(--line);padding:20px 12px}
.nav{display:block;padding:11px 13px;margin:3px 0;border-radius:10px;color:#65728a}.nav:hover,.nav.active{background:var(--pale);color:var(--blue)}
main{padding:28px clamp(14px,3vw,38px);min-width:0;max-width:1500px;width:100%;margin:auto}
h1{font-size:28px;margin:0}h2{font-size:18px}.muted,.sub{color:var(--muted)}.heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.upload{margin:22px 0;background:linear-gradient(115deg,#eef2ff,#fff);border:1px solid #dce4ff;border-radius:16px;padding:18px;display:flex;gap:15px;align-items:center;justify-content:space-between;flex-wrap:wrap}

/* NestDrive spacing fix: home upload and new-folder controls */
.upload > form{
  display:flex;
  align-items:center;
  gap:10px;
  flex-wrap:wrap;
  min-width:0;
}
.upload > form:first-child{flex:1 1 340px}
.upload > form:nth-child(2){flex:1 1 270px}
.upload > form input[type=file]{
  flex:1 1 220px;
  min-width:0;
  width:100%;
}
.upload > form input[type=text]{
  flex:1 1 170px;
  min-width:0;
  width:100%;
}
.upload > form button{
  flex:0 0 auto;
  white-space:nowrap;
}
@media(max-width:600px){
  .upload{align-items:stretch}
  .upload > form{width:100%}
  .upload > form input[type=file],
  .upload > form input[type=text]{min-width:0}
}

input[type=text],input[type=password],input[type=file]{max-width:100%;padding:10px;border:1px solid var(--line);border-radius:9px;background:white}
.categories{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:15px 0 25px}.cat,.file{background:white;border:1px solid var(--line);border-radius:14px;padding:13px;box-shadow:var(--shadow)}
.cat b,.cat small{display:block}.cat small{color:var(--muted);margin-top:6px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.pill{padding:8px 12px;border:1px solid var(--line);background:white;border-radius:9px}.pill.active{background:var(--pale);color:var(--blue)}
.files{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:12px}.files.list{display:flex;flex-direction:column}.files.list .file{display:grid;grid-template-columns:90px minmax(0,1fr) auto;align-items:center;gap:12px}.files.list .thumb{width:90px;height:64px;margin:0;grid-row:1 / span 2}.files.list .actions{margin:0;grid-column:3;grid-row:1 / span 2}.files.list .meta{margin:0}.thumb img{display:block;width:100%;height:100%;object-fit:cover}.icon-download{display:inline-grid;place-items:center;width:32px;height:32px;padding:0!important;border:1px solid var(--line);border-radius:8px;background:white;font-size:18px!important;cursor:pointer}.icon-download:hover{background:var(--pale);color:var(--blue)}.thumb{height:85px;background:#f3f5fb;border-radius:9px;display:grid;place-items:center;font-size:34px;margin-bottom:10px;overflow:hidden}.fname{font-weight:700;overflow-wrap:anywhere}.meta{font-size:12px;color:var(--muted);margin-top:5px}.actions{display:flex;gap:5px;flex-wrap:wrap;margin-top:12px}.actions button,.actions a{font-size:12px;padding:6px 8px;border:1px solid var(--line);border-radius:7px;background:white}.empty{padding:40px;text-align:center;color:var(--muted);grid-column:1/-1}
.notice{padding:10px 13px;border-radius:9px;background:#eef2ff;margin:12px 0}.danger{color:#b91c1c}
.preview-modal[hidden]{display:none}.preview-modal{position:fixed;inset:0;z-index:20;background:#101522d9;display:grid;place-items:center;padding:18px}.preview-panel{position:relative;box-sizing:border-box;background:#fff;border-radius:14px;padding:54px 14px 14px;width:fit-content;max-width:calc(100vw - 20px);height:fit-content;max-height:96dvh;min-height:0;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center}.preview-panel img{display:block;flex:0 1 auto;min-width:0;min-height:0;max-width:calc(100vw - 64px);max-height:calc(90dvh - 100px);width:auto;height:auto;object-fit:contain}.preview-panel video{display:block;flex:0 1 auto;align-self:center;min-width:0;min-height:0;width:auto;height:auto;max-width:calc(100vw - 64px);max-height:calc(90dvh - 100px);object-fit:contain}.preview-panel audio{display:block;flex:0 1 auto;align-self:center;width:min(500px,calc(100vw - 64px));max-width:100%;height:54px}.preview-panel img[hidden],.preview-panel video[hidden],.preview-panel audio[hidden]{display:none!important}.preview-close{position:absolute;top:10px;right:10px;border:1px solid var(--line);background:white;border-radius:9px;padding:8px 12px;font-weight:700}
.thumb a.preview-trigger{display:grid;place-items:center;width:100%;height:100%;position:relative}

/* File-list fallback icons only: improve contrast without touching other UI icons */
.files .thumb > .file-line-icon,
.files .thumb > a.preview-trigger > .file-line-icon {
  color: var(--ink) !important;
  stroke: var(--ink) !important;
  opacity: 1 !important;
}

.thumb .video-thumb{display:block;width:100%;height:100%;object-fit:cover;background:#e8ecf5}
.thumb .media-fallback{display:none!important}
.preview-panel video{width:auto;height:auto;max-width:calc(100vw - 64px);max-height:calc(90vh - 100px);aspect-ratio:16 / 9;background:#111}
.preview-panel audio{width:min(500px,calc(100vw - 64px));height:54px}
.preview-panel iframe{display:block;width:min(1100px,calc(100vw - 48px));height:calc(96dvh - 90px);min-height:300px;max-height:calc(96vh - 90px);border:0;background:#fff;flex:0 0 auto}
.preview-panel pre{box-sizing:border-box;width:min(1000px,calc(100vw - 64px));max-height:75vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;padding:16px;margin:0;font:14px/1.6 monospace;background:#fff;color:#222}
.preview-panel .preview-message{max-width:min(700px,calc(100vw - 80px));overflow-wrap:anywhere;text-align:center;color:#222;padding:12px}
.preview-panel iframe[hidden],.preview-panel pre[hidden],.preview-panel .preview-message[hidden]{display:none!important}




/* Sidebar theme-matched colors: all themes except Premium Black */
body:not(.theme-premium-black) #sidebar .nav {
  color: var(--blue) !important;
  transition: background-color .15s ease, color .15s ease;
}

/* Hover remains readable and uses the theme accent */
body:not(.theme-premium-black) #sidebar .nav:hover {
  background: var(--pale) !important;
  color: var(--blue) !important;
}

/* Selected item: theme accent background with white icon and label */
body:not(.theme-premium-black) #sidebar .nav.active,
body:not(.theme-premium-black) #sidebar .nav.active:hover {
  background: var(--blue) !important;
  color: #FFFFFF !important;
}

body:not(.theme-premium-black) #sidebar .nav.active .premium-nav-icon,
body:not(.theme-premium-black) #sidebar .nav.active .premium-nav-label,
body:not(.theme-premium-black) #sidebar .nav.active svg {
  color: #FFFFFF !important;
  stroke: #FFFFFF !important;
}
\n/* Sidebar sizing: regular themes match Premium Black; preserve Premium Black */
body:not(.theme-premium-black) #sidebar .nav {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 15.5px;
  font-weight: 650;
  line-height: 1.35;
}
body:not(.theme-premium-black) #sidebar .premium-nav-icon {
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  flex: 0 0 25px !important;
  width: 25px !important;
  height: 25px !important;
  padding: 0 !important;
  margin: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  filter: none !important;
  line-height: 1 !important;
}
body:not(.theme-premium-black) #sidebar .premium-nav-icon svg {
  display: block !important;
  width: 23px !important;
  height: 23px !important;
  min-width: 23px !important;
  min-height: 23px !important;
  padding: 0 !important;
  margin: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  filter: none !important;
  fill: none !important;
  stroke: currentColor !important;
  stroke-width: 1.8 !important;
  stroke-linecap: round !important;
  stroke-linejoin: round !important;
}
body:not(.theme-premium-black) #sidebar .premium-nav-label {
  font-weight: inherit;
}

/* Sliding navigation drawer */
.menu-open,.menu-close{display:none}
.shell{display:grid;grid-template-columns:240px minmax(0,1fr);gap:20px}
.shell>aside{position:relative;min-width:0;padding:16px;border-radius:0}
.theme-picker strong{display:block;margin-bottom:12px}
.theme-option{cursor:pointer;font:inherit;line-height:1.3}
.theme-emoji{flex:0 0 32px;background:transparent!important;background-color:transparent!important;box-shadow:none!important}
.preview-panel{background:var(--card)!important;color:var(--ink)!important}
.preview-close{background:var(--card)!important;color:var(--ink)!important}
@media(max-width:760px){
 .menu-open{display:block;position:fixed;top:12px;left:12px;z-index:31;
  padding:10px 13px;border:1px solid var(--line);border-radius:10px;
  background:var(--card);color:var(--ink);font-size:20px}
 .shell{display:block;padding:12px}
 .shell>aside{display:none;position:fixed;z-index:30;inset:0 auto 0 0;
  width:min(310px,85vw);height:100dvh;box-sizing:border-box;overflow-y:auto;
  border-radius:0;box-shadow:0 0 0 100vmax #0007}
 .shell>aside.sidebar-open{display:block}
 .menu-close{display:none;margin:0 0 14px auto;padding:8px 12px;
  border:1px solid var(--line);border-radius:9px;
  background:var(--card);color:var(--ink)}
 .shell>main{min-width:0}
 .top{padding-left:58px}
}

/* NestDrive theme palette */
:root{--bg:#F3EDDF;--card:#FFFCF5;--ink:#29364B;--muted:#596579;--line:#D9C7A5;--blue:#4361A8;--pale:#E5EAF5;--theme-emoji-bg:#D9C7A5;color-scheme:light}
body.theme-warm-coral{--bg:#F8D8D0;--card:#FFF1EC;--ink:#512C2A;--muted:#512C2A;--line:#D85D3D;--blue:#D85D3D;--pale:#F8D8D0;--theme-emoji-bg:#D85D3D}
body.theme-sky-high{--bg:#DCEBFA;--card:#F5FAFF;--ink:#173653;--muted:#173653;--line:#367EBB;--blue:#367EBB;--pale:#DCEBFA;--theme-emoji-bg:#367EBB}
body.theme-sunlit-gold{--bg:#FFF0BF;--card:#FFFAE8;--ink:#553A1F;--muted:#553A1F;--line:#9D6228;--blue:#9D6228;--pale:#FFF0BF;--theme-emoji-bg:#9D6228}
body.theme-earthy-tones{--bg:#E8D5B5;--card:#F8F0E3;--ink:#3F3028;--muted:#3F3028;--line:#96563E;--blue:#96563E;--pale:#E8D5B5;--theme-emoji-bg:#96563E}
body.theme-twilight-blues{--bg:#252B55;--card:#303965;--ink:#F4F2FF;--muted:#F4F2FF;--line:#A5A5D7;--blue:#A5A5D7;--pale:#252B55;--theme-emoji-bg:#A5A5D7}
body.theme-vibrant-fern{--bg:#D9EAC4;--card:#F5FAED;--ink:#213D25;--muted:#213D25;--line:#28572E;--blue:#28572E;--pale:#D9EAC4;--theme-emoji-bg:#28572E}
body.theme-emerald-green{--bg:#075447;--card:#0D6857;--ink:#F0FFF9;--muted:#F0FFF9;--line:#71D9B6;--blue:#71D9B6;--pale:#075447;--theme-emoji-bg:#71D9B6}
body.theme-celestial-night{--bg:#030811;--card:#071426;--ink:#F2F6FF;--muted:#F2F6FF;--line:#8CAED6;--blue:#8CAED6;--pale:#030811;--theme-emoji-bg:#8CAED6}
body{background:var(--bg);color:var(--ink)}
.top,aside,.cat,.file,.preview-panel{background:var(--card);color:var(--ink);border-color:var(--line)}
.nav{color:var(--ink)}
.nav:hover,.nav.active{background:var(--pale);color:var(--ink)}
input,select,textarea,.pill,.actions button,.actions a,.preview-close{background:var(--card);color:var(--ink);border-color:var(--line)}
.muted,.sub,.meta,.empty{color:var(--muted)}
.theme-picker{margin-top:18px;padding:12px;border:1px solid var(--line);border-radius:14px;background:var(--bg)}
.theme-options{
  display:grid;
  grid-template-columns:repeat(2,minmax(0,1fr));
  grid-auto-rows:64px;
  gap:10px;
}
.theme-option{
  display:flex;
  align-items:center;
  justify-content:center;
  width:100%;
  min-width:0;
  height:64px;
  padding:8px;
  border:1px solid var(--line);
  border-radius:12px;
  background:var(--card);
  color:var(--ink);
  cursor:pointer;
  font-size:0;
  transition:border-color .18s ease,transform .18s ease;
}
.theme-option:hover{
  border-color:var(--blue);
  transform:translateY(-1px);
}
.theme-option[aria-pressed=true]{
  border:2px solid var(--blue);
  font-weight:700;
}
.theme-emoji{
  display:grid;
  place-items:center;
  width:40px;
  height:40px;
  flex:0 0 40px;
  border-radius:10px;
  background:var(--theme-emoji-bg);
  color:white;
  font-size:24px;
  line-height:1;
}
.theme-option:focus-visible{
  outline:2px solid var(--blue);
  outline-offset:3px;
}
@media(max-width:360px){
  .theme-options{gap:7px;grid-auto-rows:56px}
  .theme-option{height:56px}
  .theme-emoji{width:36px;height:36px;flex-basis:36px;font-size:22px}
}

/* NestDrive colour grading: home page */
:root{
 --muted:#596579;
 --focus-ring:rgba(67,97,168,.28);
 --surface-shadow:0 12px 32px rgba(41,54,75,.075);
}
body.theme-light{--bg:#F4EBDD;--card:#FFFCF5;--ink:#17263A;--muted:#596579;--line:#B49A73;--blue:#35465D;--pale:#F4EBDD;--theme-emoji-bg:#B49A73;--focus-ring:rgba(53,70,93,.28);--surface-shadow:0 12px 32px rgba(23,38,58,.08);color-scheme:light}
body.theme-warm-coral{--muted:#87514b;--focus-ring:rgba(216,93,61,.25)}
body.theme-sky-high{--muted:#496b88;--focus-ring:rgba(54,126,187,.25)}
body.theme-sunlit-gold{--muted:#80633a;--focus-ring:rgba(157,98,40,.25)}
body.theme-earthy-tones{--muted:#755543;--focus-ring:rgba(150,86,62,.25)}
body.theme-twilight-blues{--muted:#c1c8ed;--focus-ring:rgba(165,165,215,.3)}
body.theme-vibrant-fern{--muted:#4c6845;--focus-ring:rgba(40,87,46,.24)}
body.theme-emerald-green{--muted:#b6e8d6;--focus-ring:rgba(113,217,182,.28)}
body.theme-celestial-night{--muted:#b3c5df;--focus-ring:rgba(140,174,214,.3)}

body{
 background:radial-gradient(ellipse at 8% 0%,var(--pale),transparent 58%),var(--bg);
 color:var(--ink);
 transition:background-color .22s,color .22s;
}
.top,aside,.cat,.file,.preview-panel{
 border-color:var(--line);
 box-shadow:var(--surface-shadow);
}
.top,aside,.cat,.file,.preview-panel,.theme-picker,.theme-option{
 transition:background-color .2s,border-color .2s,box-shadow .2s,color .2s,transform .18s;
}
.cat,.file,.preview-panel{border-radius:15px}
.nav{border-radius:10px;transition:background-color .18s,color .18s}
.nav:hover,.nav.active{background:var(--pale)}
input,select,textarea{
 accent-color:var(--blue);
 border-radius:10px;
}
input:focus-visible,select:focus-visible,textarea:focus-visible,
button:focus-visible,a:focus-visible{
 outline:3px solid var(--focus-ring);
 outline-offset:2px;
}
.btn,.actions button,.actions a{
 transition:filter .18s,transform .18s,box-shadow .18s;
}
.btn:hover,.actions button:hover,.actions a:hover{
 filter:brightness(.96);
 box-shadow:0 4px 12px var(--focus-ring);
}
.theme-option:hover{border-color:var(--blue);transform:translateY(-1px)}
.theme-option[aria-pressed=true]{box-shadow:0 0 0 3px var(--focus-ring)}
.theme-emoji{box-shadow:inset 0 1px 0 #ffffff30}
.muted,.sub,.meta,.empty{color:var(--muted)}


/* Premium Black theme: deep navigation, charcoal content, bright text */

/* Sunny Ivory finishing styles */
body.theme-light .top{
  background:#17263A;color:#FFFCF5;border-color:#B49A73;
}
body.theme-light .top .brand{color:#FFFCF5}
body.theme-light .top .brand span{color:#B49A73}
body.theme-light .shell>aside,
body.theme-light aside,
body.theme-light #sidebar{
  background:#17263A;color:#FFFCF5;border-color:#B49A73;
}
body.theme-light aside .nav,
body.theme-light #sidebar .nav{color:#FFFCF5}
body.theme-light aside .nav:hover,
body.theme-light aside .nav.active,
body.theme-light #sidebar .nav:hover,
body.theme-light #sidebar .nav.active{
  background:#35465D;color:#FFFCF5;
}
body.theme-light .theme-picker{
  background:#17263A;color:#FFFCF5;border-color:#B49A73;
}
body.theme-light .theme-option{
  background:#35465D;color:#FFFCF5;border-color:#B49A73;
}
body.theme-light .theme-option.active,
body.theme-light .theme-option[aria-pressed="true"]{
  outline:2px solid #B49A73;outline-offset:1px;
}
body.theme-light .upload{
  background:linear-gradient(115deg,#35465D,#17263A);
  color:#FFFCF5;border-color:#B49A73;
}
body.theme-light .upload button{
  background:#FFFCF5;color:#17263A;
}
body.theme-light .cat,
body.theme-light .file,
body.theme-light .preview-panel{color:#17263A}

body.theme-premium-black{
 --bg:#080B12;--card:#293342;--ink:#F7F9FC;--muted:#C1CBD9;
 --line:#526782;--blue:#8DB7FF;--pale:#3A4A60;--theme-emoji-bg:#526782;
 --focus-ring:rgba(141,183,255,.42);--shadow:0 12px 32px rgba(0,0,0,.30);
 color-scheme:dark;
}
body.theme-premium-black{background:radial-gradient(ellipse at 10% 0%,#172236 0%,transparent 58%),var(--bg);color:var(--ink)}
body.theme-premium-black .top,
body.theme-premium-black aside,
body.theme-premium-black #sidebar{
 background:#111722;color:var(--ink);border-color:var(--line);
}
body.theme-premium-black .cat,
body.theme-premium-black .file,
body.theme-premium-black .preview-panel,
body.theme-premium-black .theme-picker,
body.theme-premium-black .theme-option{
 background:var(--card);color:var(--ink);border-color:var(--line);
}
body.theme-premium-black .nav,
body.theme-premium-black .muted,
body.theme-premium-black .sub,
body.theme-premium-black .meta,
body.theme-premium-black .empty{color:var(--muted)}
body.theme-premium-black .nav:hover,
body.theme-premium-black .nav.active,
body.theme-premium-black .theme-option[aria-pressed=true]{
 background:var(--pale);color:var(--ink);border-color:var(--blue);
}
body.theme-premium-black input,
body.theme-premium-black select,
body.theme-premium-black textarea,
body.theme-premium-black .pill,
body.theme-premium-black .actions button,
body.theme-premium-black .actions a,
body.theme-premium-black .preview-close{
 background:var(--card);color:var(--ink);border-color:var(--line);
}
body.theme-premium-black .theme-emoji{background:var(--line);color:var(--ink)}
body.theme-premium-black button:focus-visible,
body.theme-premium-black a:focus-visible,
body.theme-premium-black input:focus-visible,
body.theme-premium-black select:focus-visible,
body.theme-premium-black textarea:focus-visible{
 outline:3px solid var(--focus-ring);outline-offset:2px;
}
body.theme-premium-black .btn.blue{
 background:var(--blue);color:#080B12;border-color:var(--blue);
}
body.theme-premium-black .top,
body.theme-premium-black aside,
body.theme-premium-black .cat,
body.theme-premium-black .file,
body.theme-premium-black .preview-panel{box-shadow:var(--shadow)}

@media(prefers-reduced-motion:reduce){*{transition:none!important}}

/* Sunny Ivory: match buttons to the warm brown-gold Nest logo */
body.theme-light .upload > form button{
  background:#B49A73!important;
  color:#FFFCF5!important;
  border-color:#B49A73!important;
}


/* NestDrive accent consistency: upload and folder buttons */
.upload > form button{
  background:var(--blue);
  color:var(--card);
  border:1px solid var(--blue);
}
.upload > form button:hover{
  filter:brightness(.94);
}


/* Premium Black — refined dashboard surfaces and accents */
body.theme-premium-black {
  background:
    radial-gradient(ellipse at 12% 0%, rgba(40,124,120,.10), transparent 42%),
    #090B0F;
}
body.theme-premium-black main { color: #F3F1EC; }
body.theme-premium-black h1,
body.theme-premium-black h2,
body.theme-premium-black .cat b,
body.theme-premium-black .fname { color: #F3F1EC; }

body.theme-premium-black .upload {
  background: linear-gradient(120deg, #171A20, #111318 75%);
  border: 1px solid #414650;
  box-shadow: 0 12px 30px rgba(0,0,0,.22);
}
body.theme-premium-black .cat,
body.theme-premium-black .file,
body.theme-premium-black .preview-panel {
  background: #171A20;
  color: #F3F1EC;
  border: 1px solid #414650;
  box-shadow: 0 8px 24px rgba(0,0,0,.20);
}
body.theme-premium-black .cat:hover,
body.theme-premium-black .file:hover {
  border-color: rgba(40,124,120,.85);
}
body.theme-premium-black .thumb {
  background: #22262D;
  border: 1px solid rgba(192,197,206,.10);
}
body.theme-premium-black .toolbar .pill {
  background: #171A20;
  color: #C0C5CE;
  border-color: #414650;
}
body.theme-premium-black .toolbar .pill:hover,
body.theme-premium-black .toolbar .pill.active {
  background: rgba(40,124,120,.18);
  color: #F3F1EC;
  border-color: #287C78;
}
body.theme-premium-black .btn.blue {
  background: #C5A45D;
  color: #090B0F;
  border: 1px solid #D8BB78;
  font-weight: 750;
}
body.theme-premium-black .btn.blue:hover {
  background: #D8BB78;
  border-color: #E7D19A;
}
body.theme-premium-black .btn.plain,
body.theme-premium-black .actions button,
body.theme-premium-black .actions a,
body.theme-premium-black .preview-close {
  background: #22262D;
  color: #C0C5CE;
  border-color: #414650;
}
body.theme-premium-black .actions button:hover,
body.theme-premium-black .actions a:hover,
body.theme-premium-black .preview-close:hover {
  background: rgba(40,124,120,.18);
  color: #F3F1EC;
  border-color: #287C78;
}
body.theme-premium-black input,
body.theme-premium-black select,
body.theme-premium-black textarea {
  background: #111318;
  color: #F3F1EC;
  border-color: #414650;
}
body.theme-premium-black input::placeholder,
body.theme-premium-black textarea::placeholder { color: #929AA5; }
body.theme-premium-black .notice {
  background: rgba(40,124,120,.14);
  color: #F3F1EC;
  border: 1px solid rgba(40,124,120,.45);
}
body.theme-premium-black .top,
body.theme-premium-black aside,
body.theme-premium-black #sidebar {
  background: #111318;
  border-color: #414650;
}
body.theme-premium-black .nav:hover,
body.theme-premium-black .nav.active {
  background: rgba(40,124,120,.18);
  color: #F3F1EC;
  border-color: #287C78;
}
body.theme-premium-black button:focus-visible,
body.theme-premium-black a:focus-visible,
body.theme-premium-black input:focus-visible,
body.theme-premium-black select:focus-visible,
body.theme-premium-black textarea:focus-visible {
  outline: 2px solid #C5A45D;
  outline-offset: 3px;
}

/* Compact artwork-only theme switch */
.theme-picker[hidden] { display: none !important; }

.theme-cycle-button {
  display: inline-flex !important;
  align-items: stretch !important;
  justify-content: stretch !important;
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0 !important;
  height: auto !important;
  padding: 0 !important;
  overflow: hidden !important;
  border: 1px solid var(--line) !important;
  border-radius: 9px !important;
  background: transparent !important;
  color: var(--ink) !important;
  cursor: pointer;
  box-shadow: var(--shadow);
}

.theme-cycle-button:hover { transform: translateY(-1px); }

.theme-cycle-button:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 3px;
}

#theme-cycle-art {
  display: flex !important;
  width: 100% !important;
  height: 100% !important;
  align-items: stretch !important;
  justify-content: stretch !important;
  padding: 0 !important;
  font-size: 0 !important;
  line-height: 0 !important;
}

#theme-cycle-art svg {
  display: block !important;
  flex: 1 1 auto !important;
  width: 100% !important;
  height: 100% !important;
  min-width: 100% !important;
  min-height: 100% !important;
}

.drive-heading-right {
  display: flex;
  flex-direction: row !important;
  align-items: center !important;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}

.drive-heading .storage-usage {
  margin-top: 5px;
  text-align: left;
}

.current-theme-indicator {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 4px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  background: var(--card);
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}
.storage-usage {
  font-size: 12px;
  text-align: right;
}
body.theme-premium-black .current-theme-indicator {
  color: #C5A45D;
  background: #171A20;
  border-color: #414650;
}
@media (max-width: 760px) {
  .drive-heading-right { align-items: flex-start; }
  .storage-usage { text-align: left; }
}

"""

LOGIN_TEMPLATE = """<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font:16px system-ui;background:#f4f6fc;display:grid;place-items:center;min-height:95vh;margin:0}section{background:white;padding:30px;border-radius:20px;width:min(390px,90vw);box-shadow:0 10px 40px #0001}input,button{width:100%;padding:13px;border-radius:10px;margin:7px 0;box-sizing:border-box}input{border:1px solid #ddd}button{background:#4361ee;color:white;border:0;font-weight:bold}.muted{color:#718096}body.theme-dusk{background:#282536;color:#f5eefa}body.theme-night{background:#101827;color:#edf4ff}body.theme-dusk section,body.theme-night section{background:#393447;color:inherit}body.theme-night section{background:#1c283a}body.theme-dusk input,body.theme-night input{background:#20202d;color:#fff;border-color:#554a63}body.theme-night input{background:#101827;border-color:#35445a}body.theme-dusk .muted,body.theme-night .muted{color:#c0b4cf}body.theme-night .muted{color:#a9b9d0}body.theme-dusk a,body.theme-night a{color:#c5a5ff}
</style>
<section><h1>Nest<span style="color:#4361ee">Drive</span></h1><p class="muted">{{ subtitle }}</p>
{% for message in get_flashed_messages() %}<p>{{ message }}</p>{% endfor %}
<form method="post"><input name="username" autocomplete="username" placeholder="Username" required>
<input type="password" name="password" autocomplete="{{ 'new-password' if register else 'current-password' }}" placeholder="Password" required>
{% if register %}<input type="password" name="confirm" autocomplete="new-password" placeholder="Confirm password" required>{% endif %}
<button>{{ 'Create account' if register else 'Sign in' }} →</button></form>
{% if register %}<p>Already registered? <a href="{{ url_for('login') }}">Sign in</a></p>{% else %}<p>New here? <a href="{{ url_for('register') }}">Create an account</a></p>{% endif %}
</section>"""

HOME_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NestDrive{% if is_folder %} · {{ folder_name }}{% endif %}</title><style>""" + CSS + """
/* Premium Black — final luxury palette override */
body.theme-premium-black {
  background: #090B0F !important;
  color: #F3F1EC;
  --bg: #090B0F;
  --card: #171A20;
  --ink: #F3F1EC;
  --muted: #C0C5CE;
  --line: #414650;
  --blue: #C5A45D;
  --pale: #C5A45D;
  --focus-ring: rgba(197,164,93,.55);
}
body.theme-premium-black .top,
body.theme-premium-black aside,
body.theme-premium-black #sidebar {
  background: #111318 !important;
  color: #F3F1EC;
  border-color: #414650;
}
body.theme-premium-black .cat,
body.theme-premium-black .file,
body.theme-premium-black .preview-panel,
body.theme-premium-black .theme-picker,
body.theme-premium-black .theme-option {
  background: #171A20;
  color: #F3F1EC;
  border-color: #414650;
}
body.theme-premium-black .toolbar .pill,
body.theme-premium-black .nav {
  background: #111318;
  color: #C5A45D;
  border: 1px solid #414650;
}
body.theme-premium-black .toolbar .pill:hover,
body.theme-premium-black .toolbar .pill.active,
body.theme-premium-black .nav:hover,
body.theme-premium-black .nav.active {
  background: #C5A45D !important;
  color: #287C78 !important;
  border-color: #C5A45D !important;
}
body.theme-premium-black .toolbar .pill.active *,
body.theme-premium-black .nav.active *,
body.theme-premium-black .toolbar .pill.active svg,
body.theme-premium-black .nav.active svg {
  color: #287C78 !important;
  fill: currentColor;
}
body.theme-premium-black .cat:hover,
body.theme-premium-black .file:hover {
  border-color: #287C78;
}
body.theme-premium-black .btn.blue,
body.theme-premium-black .upload > form button.btn.blue {
  background: #C5A45D !important;
  color: #10233F !important;
  border: 1px solid #D8BB78 !important;
  font-weight: 750;
}
body.theme-premium-black .btn.blue:hover,
body.theme-premium-black .upload > form button.btn.blue:hover {
  background: #D8BB78 !important;
  color: #10233F !important;
}
body.theme-premium-black .btn.plain,
body.theme-premium-black .actions button,
body.theme-premium-black .actions a,
body.theme-premium-black .preview-close {
  background: #C5A45D;
  color: #10233F;
  border-color: #C5A45D;
}
body.theme-premium-black .btn.plain:hover,
body.theme-premium-black .actions button:hover,
body.theme-premium-black .actions a:hover,
body.theme-premium-black .preview-close:hover {
  background: #D8BB78;
  color: #10233F;
  border-color: #D8BB78;
}
body.theme-premium-black .fname,
body.theme-premium-black .fname a {
  color: #C5A45D;
}
body.theme-premium-black .cat b {
  color: #F3F1EC;
}
body.theme-premium-black input,
body.theme-premium-black select,
body.theme-premium-black textarea {
  background: #111318;
  color: #F3F1EC;
  border-color: #414650;
}
body.theme-premium-black .thumb {
  background: #22262D;
  border-color: #414650;
}
body.theme-premium-black .notice {
  background: #111F20;
  color: #F3F1EC;
  border-color: #287C78;
}
body.theme-premium-black button:focus-visible,
body.theme-premium-black a:focus-visible,
body.theme-premium-black input:focus-visible,
body.theme-premium-black select:focus-visible,
body.theme-premium-black textarea:focus-visible {
  outline: 2px solid #C5A45D;
  outline-offset: 3px;
}


/* Premium Black — seamless navigation refinement */
body.theme-premium-black #sidebar .nav {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  box-sizing: border-box;
  padding: 12px 15px;
  margin: 3px 0;
  border: 1px solid transparent !important;
  border-radius: 10px;
  background: transparent !important;
  color: #C5A45D !important;
  font-size: 15.5px;
  font-weight: 650;
  line-height: 1.35;
  text-decoration: none;
  box-shadow: none !important;
  transition: background .16s ease, color .16s ease;
}
body.theme-premium-black #sidebar .premium-nav-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 25px;
  width: 25px;
  height: 25px;
  color: currentColor;
}
body.theme-premium-black #sidebar .premium-nav-icon svg {
  display: block;
  width: 23px;
  height: 23px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
body.theme-premium-black #sidebar .premium-nav-label {
  color: inherit;
  font-weight: inherit;
}
body.theme-premium-black #sidebar .nav:hover {
  background: rgba(197,164,93,.09) !important;
  color: #D8BB78 !important;
  border-color: transparent !important;
}
body.theme-premium-black #sidebar .nav.active,
body.theme-premium-black #sidebar .nav.active:hover {
  background: #C5A45D !important;
  color: #287C78 !important;
  border-color: transparent !important;
  box-shadow: none !important;
}
body.theme-premium-black #sidebar .nav.active .premium-nav-icon,
body.theme-premium-black #sidebar .nav.active .premium-nav-label {
  color: #287C78 !important;
}
body.theme-premium-black .top a.btn.plain,
body.theme-premium-black .top a[href="/logout"] {
  background: #C5A45D !important;
  color: #D7E6FF !important;
  border: 1px solid #C5A45D !important;
  font-weight: 750;
}
body.theme-premium-black .top a.btn.plain:hover,
body.theme-premium-black .top a[href="/logout"]:hover {
  background: #D8BB78 !important;
  color: #D7E6FF !important;
}
body.theme-premium-black .upload > form button.btn.blue,
body.theme-premium-black .upload > form button.btn.plain {
  background: #C5A45D !important;
  color: #D7E6FF !important;
  border-color: #C5A45D !important;
  font-weight: 750;
  font-size: 15px;
  min-height: 44px;
}
body.theme-premium-black .upload > form button.btn.blue:hover,
body.theme-premium-black .upload > form button.btn.plain:hover {
  background: #D8BB78 !important;
  color: #D7E6FF !important;
}

.thumb .file-line-icon{width:42px;height:42px;display:block}

/* Premium graphite styling: file-list thumbnails and all file-type icons only */
.files .thumb {
  background: #e4e8ef;
  border: 1px solid #c5ccd7;
}
.files .thumb > .file-line-icon,
.files .thumb > a.preview-trigger > .file-line-icon {
  color: #263247 !important;
  stroke: #263247 !important;
  opacity: 1 !important;
}
.files .file {
  border-color: #c5ccd7;
}
.files .fname {
  color: #202938;
}
.files .meta {
  color: #566174;
}
.files .actions button,
.files .actions a {
  color: #263247;
  border-color: #c5ccd7;
  background: #f4f6f9;
}


/* Premium Black: only the upload, create-folder and logout buttons */
body.theme-premium-black header.top a[href="/logout"],
body.theme-premium-black section.upload form[action="/upload"] button.btn.blue,
body.theme-premium-black section.upload form[action="/mkdir"] button.btn.plain {
  background:#C5A45D !important;
  color:#D7E6FF !important;
  border:1px solid #C5A45D !important;
  font-weight:750 !important;
}
body.theme-premium-black header.top a[href="/logout"]:hover,
body.theme-premium-black section.upload form[action="/upload"] button.btn.blue:hover,
body.theme-premium-black section.upload form[action="/mkdir"] button.btn.plain:hover {
  background:#D8BB78 !important;
  color:#D7E6FF !important;
  border-color:#D8BB78 !important;
}

/* Category toolbar: bold labels only; preserve each theme's colors */
.toolbar .pill {
  font-weight: 700 !important;
}

/* Premium Black: gold upload, folder and logout buttons only */
body.theme-premium-black header.top a[href="/logout"],
body.theme-premium-black section.upload form[action="/upload"] button.btn.blue,
body.theme-premium-black section.upload form[action="/mkdir"] button.btn.plain {
  background: #C5A45D !important;
  color: #287C78 !important;
  border-color: #C5A45D !important;
  font-weight: 750 !important;
}

body.theme-premium-black header.top a[href="/logout"]:hover,
body.theme-premium-black section.upload form[action="/upload"] button.btn.blue:hover,
body.theme-premium-black section.upload form[action="/mkdir"] button.btn.plain:hover {
  background: #D8BB78 !important;
  color: #287C78 !important;
  border-color: #D8BB78 !important;
}


/* Final Premium Black logout-only color fix */
body.theme-premium-black header.top a.btn.plain[href="/logout"],
body.theme-premium-black header.top a.btn.plain[href="/logout"]:visited {
  background: #C5A45D !important;
  color: #287C78 !important;
  border: 1px solid #C5A45D !important;
}
body.theme-premium-black header.top a.btn.plain[href="/logout"]:hover {
  background: #D8BB78 !important;
  color: #287C78 !important;
  border-color: #D8BB78 !important;
}


/* NestDrive nine-theme icon alignment fix; Premium Black excluded */
body:not(.theme-premium-black) .theme-option:not([data-theme="theme-premium-black"]) .theme-emoji {
  width: 1.4em !important;
  height: 1.4em !important;
  flex: 0 0 1.4em !important;
  display: inline-grid !important;
  place-items: center !important;
  line-height: 1 !important;
  vertical-align: middle !important;
  padding: 0 !important;
  margin: 0 !important;
  background: transparent !important;
  background-color: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
}
body:not(.theme-premium-black) .theme-option:not([data-theme="theme-premium-black"]) .theme-emoji svg {
  width: 100% !important;
  height: 100% !important;
  display: block !important;
}



/* White text for Upload and Folder buttons in three themes */

/* White text for Upload and Folder buttons in three themes */
body.theme-celestial-night .upload button,
body.theme-emerald-green .upload button,
body.theme-twilight-blues .upload button,
body.theme-celestial-night .upload input[type="submit"],
body.theme-emerald-green .upload input[type="submit"],
body.theme-twilight-blues .upload input[type="submit"] {
  color: #FFFFFF !important;
}

/* Theme-matched logout button: regular themes only */

/* White logout text for the five requested themes */
body.theme-warm-coral header.top a.btn.plain[href="/logout"],
body.theme-sky-high header.top a.btn.plain[href="/logout"],
body.theme-sunlit-gold header.top a.btn.plain[href="/logout"],
body.theme-earthy-tones header.top a.btn.plain[href="/logout"],
body.theme-vibrant-fern header.top a.btn.plain[href="/logout"] {
  color: #FFFFFF !important;
}

body:not(.theme-light):not(.theme-premium-black) header.top a.btn.plain[href="/logout"],
body:not(.theme-light):not(.theme-premium-black) header.top a.btn.plain[href="/logout"]:visited {
  background: var(--blue) !important;
  border-color: var(--blue) !important;
}
body:not(.theme-light):not(.theme-premium-black) header.top a.btn.plain[href="/logout"]:hover {
  filter: brightness(0.88);
}

/* Sunny Ivory: Nest-brown logout and selected sidebar item */

/* Sunny Ivory sidebar contrast fix */
body.theme-light #sidebar .nav:not(.active),
body.theme-light #sidebar .nav:not(.active) .premium-nav-label,
body.theme-light #sidebar .nav:not(.active) .premium-nav-icon,
body.theme-light #sidebar .nav:not(.active) svg {
  color: #B49A73 !important;
  stroke: #B49A73 !important;
}

body.theme-light #sidebar .nav.active,
body.theme-light #sidebar .nav.active:hover {
  background: #B49A73 !important;
  color: #FFFFFF !important;
}

body.theme-light #sidebar .nav.active .premium-nav-label,
body.theme-light #sidebar .nav.active .premium-nav-icon,
body.theme-light #sidebar .nav.active svg {
  color: #FFFFFF !important;
  stroke: #FFFFFF !important;
}

body.theme-light header.top a.btn.plain[href="/logout"] {
  background: #B49A73 !important;
  border-color: #B49A73 !important;
}
body.theme-light header.top a.btn.plain[href="/logout"]:hover {
  background: #9F855E !important;
  border-color: #9F855E !important;
}
body.theme-light #sidebar .nav.active,
body.theme-light #sidebar .nav.active:hover {
  background: #B49A73 !important;
  color: #FFFFFF !important;
}
body.theme-light #sidebar .nav.active .premium-nav-icon,
body.theme-light #sidebar .nav.active .premium-nav-label,
body.theme-light #sidebar .nav.active svg {
  color: #FFFFFF !important;
  stroke: #FFFFFF !important;
}

/* NestDrive taskbar: square corners in desktop and mobile views */
#sidebar,
.shell>aside,
.shell>aside.sidebar-open {
  border-radius: 0 !important;
}

/* Hamburger is the only mobile drawer control */
.menu-close {
  display: none !important;
}

/* Desktop: keep My Drive anchored at the top when category content changes */
@media (min-width: 761px) {
  .shell > main {
    margin-top: 0;
    margin-bottom: 0;
  }
}


/* Premium Black: restore white file-list contrast */
body.theme-premium-black .files .thumb {
  background: #24272d !important;
  border-color: #454b55 !important;
}
body.theme-premium-black .files .thumb > .file-line-icon,
body.theme-premium-black .files .thumb > a.preview-trigger > .file-line-icon {
  color: #ffffff !important;
  stroke: #ffffff !important;
  opacity: 1 !important;
}
body.theme-premium-black .files .fname {
  color: #ffffff !important;
}
body.theme-premium-black .files .meta {
  color: #c5cbd4 !important;
}
body.theme-premium-black .files .actions button,
body.theme-premium-black .files .actions a {
  color: #ffffff !important;
  background: #30343c !important;
  border-color: #555d69 !important;
}




</style></head><body class="theme-light"><button type="button" id="menu-open" class="menu-open" aria-label="Open sidebar">☰</button>
<header class="top"><a class="brand" href="/">Nest<span>Drive</span></a><form class="search"><input name="q" value="{{ query }}" placeholder="Search your files…"></form><a class="btn plain" href="/logout">Log out ({{ username }})</a></header>
<div class="shell"><aside id="sidebar">{% for c in ['All files','Folders','Images','Documents','Videos','Audio','Other','Trash'] %}
<a class="nav {{ 'active' if selected==c else '' }}" href="/?category={{ c }}">
<span class="premium-nav-icon" aria-hidden="true">
{% if c == 'All files' %}
<svg viewBox="0 0 24 24"><path d="m3 10 9-7 9 7"/><path d="M5 9v12h14V9M9 21v-7h6v7"/></svg>
{% elif c == 'Folders' %}
<svg viewBox="0 0 24 24"><path d="M3 6.5h7l2 2h9v10.5H3z"/><path d="M3 6.5V5h7l2 2"/></svg>
{% elif c == 'Images' %}
<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m21 15-5-5L5 20"/></svg>
{% elif c == 'Documents' %}
<svg viewBox="0 0 24 24"><path d="M6 3h8l5 5v13H6z"/><path d="M14 3v6h5M9 13h7M9 17h7"/></svg>
{% elif c == 'Videos' %}
<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg>
{% elif c == 'Audio' %}
<svg viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
{% elif c == 'Other' %}
<svg viewBox="0 0 24 24"><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></svg>
{% else %}
<svg viewBox="0 0 24 24"><path d="M3 6h18M9 6V4h6v2m-9 0 1 14h10l1-14M10 10v6m4-6v6"/></svg>
{% endif %}
</span><span class="premium-nav-label">{{ c }}</span></a>
{% endfor %}
<div class="theme-picker" id="theme-picker" hidden>
<strong>Choose a theme</strong>
<div class="theme-options">
<button class="theme-option" data-theme="theme-light" aria-pressed="true"><span class="theme-emoji">☀️</span></button>
<button class="theme-option" data-theme="theme-warm-coral" aria-pressed="false"><span class="theme-emoji">🌺</span></button>
<button class="theme-option" data-theme="theme-sky-high" aria-pressed="false"><span class="theme-emoji">☁️</span></button>
<button class="theme-option" data-theme="theme-sunlit-gold" aria-pressed="false"><span class="theme-emoji">🌞</span></button>
<button class="theme-option" data-theme="theme-earthy-tones" aria-pressed="false"><span class="theme-emoji">🏜️</span></button>
<button class="theme-option" data-theme="theme-twilight-blues" aria-pressed="false"><span class="theme-emoji">🌃</span></button>
<button class="theme-option" data-theme="theme-vibrant-fern" aria-pressed="false"><span class="theme-emoji">🍃</span></button>
<button class="theme-option" data-theme="theme-emerald-green" aria-pressed="false"><span class="theme-emoji">💎</span></button>
<button class="theme-option" data-theme="theme-celestial-night" aria-pressed="false" aria-label="Celestial Night"><span class="theme-emoji"><svg class="theme-art" viewBox="0 0 220 180" role="img" aria-label="Spiral galaxy and stars"><defs><radialGradient id="cnGalaxyBg"><stop stop-color="#55458f"/><stop offset=".48" stop-color="#202957"/><stop offset="1" stop-color="#050914"/></radialGradient><linearGradient id="cnGalaxyNebula" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f5a4e8"/><stop offset=".5" stop-color="#858cff"/><stop offset="1" stop-color="#55dce9" stop-opacity="0"/></linearGradient><radialGradient id="cnGalaxyCore"><stop stop-color="#fff"/><stop offset="1" stop-color="#b5a8ff" stop-opacity="0"/></radialGradient></defs><rect width="220" height="180" fill="url(#cnGalaxyBg)"/><ellipse cx="110" cy="90" rx="105" ry="36" transform="rotate(-28 110 90)" fill="url(#cnGalaxyNebula)" opacity=".8"/><ellipse cx="110" cy="90" rx="77" ry="22" transform="rotate(-28 110 90)" fill="none" stroke="#e2d6ff" stroke-width="1.5"/><ellipse cx="110" cy="90" rx="51" ry="13" transform="rotate(-28 110 90)" fill="none" stroke="#9de7f3" stroke-width="1.3"/><circle cx="110" cy="90" r="28" fill="url(#cnGalaxyCore)"/><circle cx="110" cy="90" r="6" fill="#fff5ff"/><g fill="#fff"><circle cx="20" cy="25" r="1.5"/><circle cx="49" cy="49" r="1"/><circle cx="82" cy="18" r="1.4"/><circle cx="144" cy="22" r="1.1"/><circle cx="188" cy="35" r="1.7"/><circle cx="199" cy="73" r="1"/><circle cx="25" cy="94" r="1.3"/><circle cx="43" cy="151" r="1.6"/><circle cx="79" cy="158" r="1"/><circle cx="148" cy="151" r="1.5"/><circle cx="196" cy="132" r="1.3"/><circle cx="174" cy="108" r="1"/><circle cx="17" cy="65" r="1"/></g><g stroke="#fff" stroke-width="1"><path d="M67 73v8M63 77h8M171 57v9M166.5 61.5h9M34 122v7M30.5 125.5h7"/></g></svg></span></button>
<button class="theme-option" data-theme="theme-premium-black" aria-pressed="false"><span class="theme-emoji"><svg viewBox="0 0 64 64" aria-hidden="true" focusable="false"><defs><linearGradient id="premiumCrownGold" x1="0" y1="0" x2="0.9" y2="1"><stop offset="0%" stop-color="#E7D19A"/><stop offset="48%" stop-color="#C5A45D"/><stop offset="100%" stop-color="#9C7838"/></linearGradient><linearGradient id="premiumCrownSilver" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#E2E5E9"/><stop offset="100%" stop-color="#929AA5"/></linearGradient></defs><path d="M10 19 22 29 31.5 10 41 29 54 19 49 43H15Z" fill="url(#premiumCrownGold)" stroke="#E7D19A" stroke-width="1.4" stroke-linejoin="round"/><path d="M15 43H49L46.5 49H17.5Z" fill="url(#premiumCrownSilver)" stroke="#C0C5CE" stroke-width="1.2" stroke-linejoin="round"/><path d="M21 34H43" stroke="#FFF0C9" stroke-width="1.4" stroke-linecap="round" opacity=".8"/><path d="M31.5 22 35 25.5 31.5 29 28 25.5Z" fill="#287C78" stroke="#73B7AD" stroke-width="1.1"/><circle cx="10" cy="17.5" r="2.2" fill="#E7D19A"/><circle cx="31.5" cy="8.5" r="2.2" fill="#E7D19A"/><circle cx="54" cy="17.5" r="2.2" fill="#E7D19A"/></svg></span></button>
</div></div></aside>
<main><div class="heading drive-heading"><div>{% if is_folder %}<h1>{{ folder_name }}</h1><p class="sub"><a href="{% if parent %}{{ url_for("folder", folder_path=parent) }}{% else %}/{% endif %}">← My Drive{% if parent %} / {{ parent }}{% endif %}</a></p>{% else %}<h1>My Drive</h1><p class="sub">Your files are private to your account.</p>{% endif %}<div class="muted storage-usage">{{ used }} logical storage used</div></div><div class="drive-heading-right"><button type="button" id="theme-cycle-button" class="theme-cycle-button" aria-label="Change theme" title="Change theme"><span id="theme-cycle-art" aria-hidden="true">☀️</span></button></div></div>
{% for message in get_flashed_messages() %}<div class="notice">{{ message }}</div>{% endfor %}
<section class="upload"><form method="post" action="/upload" enctype="multipart/form-data"><input type="file" name="file" required>{% if is_folder %}<input type="hidden" name="parent" value="{{ folder_path }}">{% endif %}<button class="btn blue">Upload file ↑</button></form>
<form method="post" action="/mkdir">{% if is_folder %}<input type="hidden" name="parent" value="{{ folder_path }}">{% endif %}<input type="text" name="name" placeholder="New folder name" required><button class="btn plain">＋ Folder</button></form></section>

<div class="heading"><h2>{% if is_folder %}Contents{% else %}{{ selected }}{% endif %} · {{ shown|length }} items</h2>{% if not is_folder %}<div class="view-switch"><a class="pill {{ 'active' if layout=='grid' else '' }}" href="/?category={{ selected|urlencode }}&q={{ query|urlencode }}&layout=grid">▦ Grid</a> <a class="pill {{ 'active' if layout=='list' else '' }}" href="/?category={{ selected|urlencode }}&q={{ query|urlencode }}&layout=list">☷ List</a></div>{% endif %}</div>{% if not is_folder %}<div class="toolbar">{% for c in ['All files','Folders','Images','Documents','Videos','Audio','Other','Trash'] %}<a class="pill {{ 'active' if selected==c else '' }}" href="/?category={{ c }}&q={{ query|urlencode }}">{{ c }}</a>{% endfor %}</div>{% endif %}
{% macro line_icon(kind) -%}<svg class="file-line-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
{% if kind == 'Folders' %}<path d="M3 6.5h7l2 2h9v10.5H3z"/><path d="M3 6.5V5h7l2 2"/>
{% elif kind == 'Images' %}<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m21 15-5-5L5 20"/>
{% elif kind == 'Videos' %}<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>
{% elif kind == 'Audio' %}<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
{% elif kind == 'Documents' %}<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v6h5M9 13h7M9 17h7"/>
{% elif kind == 'Trash' %}<path d="M3 6h18M9 6V4h6v2m-9 0 1 14h10l1-14M10 10v6m4-6v6"/>
{% else %}<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/>
{% endif %}</svg>{%- endmacro %}
<div class="files {{ layout }}">{% for f in shown %}<article class="file"><div class="thumb thumb-kind-{{ f.kind|lower }}">{% if f.kind in ['Images', 'Videos', 'Audio'] and selected != 'Trash' %}<a class="preview-trigger" data-kind="{{ f.kind }}" data-name="{{ f.name }}" href="/preview/{{ f.id }}" title="Preview {{ f.name }}">{% if f.kind == 'Images' %}<img src="/preview/{{ f.id }}" alt="{{ f.name }}" loading="lazy">{% elif f.kind == 'Videos' %}<video class="video-thumb" data-src="/preview/{{ f.id }}" muted playsinline preload="metadata" aria-label="{{ f.name }}"></video><span class="media-fallback">{{ line_icon(f.kind) }}</span>{% else %}{{ line_icon(f.kind) }}{% endif %}</a>{% elif f.kind not in ['Folders', 'Trash'] and selected != 'Trash' %}<a class="preview-trigger" data-kind="{{ f.kind }}" data-name="{{ f.name }}" href="/preview/{{ f.id }}" title="Preview {{ f.name }}">{{ line_icon(f.kind) }}</a>{% else %}{{ line_icon(f.kind) }}{% endif %}</div><div class="fname">
{% if f.kind == 'Folders' %}
<a href="/folder/{{ f.path|urlencode }}">{{ f.name }}</a>
{% elif f.kind != 'Trash' %}
<a class="preview-trigger" data-kind="{{ f.kind }}" data-name="{{ f.name }}" href="/preview/{{ f.id }}">{{ f.name }}</a>
{% else %}
{{ f.name }}
{% endif %}
</div><div class="meta">{{ f.kind }} · {{ f.size }}</div><div class="actions">
{% if f.kind=='Folders' %}<a href="/folder/{{ f.path|urlencode }}">Open</a><a class="icon-download" href="{{ url_for('download_folder', folder_path=f.path) }}" title="Download folder as ZIP" aria-label="Download folder as ZIP">↓</a>{% elif f.kind!='Trash' %}<a class="icon-download" href="/download/{{ f.id }}" title="Download file" aria-label="Download file">↓</a>{% endif %}
{% if selected=='Trash' %}<form method="post" action="/restore"><input type="hidden" name="id" value="{{ f.id }}"><button>Restore</button></form><form method="post" action="/purge" onsubmit="return confirm('Permanently delete?')"><input type="hidden" name="id" value="{{ f.id }}"><button>Delete</button></form>
{% else %}<button type="button" onclick="document.getElementById('rename-{{ f.id }}').hidden=false;document.getElementById('rename-{{ f.id }}').querySelector('input[name=new]').focus()">Rename</button><form id="rename-{{ f.id }}" method="post" action="/rename" hidden><input type="hidden" name="id" value="{{ f.id }}"><input name="new" placeholder="New name" value="{{ f.name.rsplit('.', 1)[0] if '.' in f.name and not f.name.startswith('.') else f.name }}" required style="width:120px"><button type="submit">Save</button><button type="button" onclick="this.form.hidden=true">Cancel</button></form><form method="post" action="/delete" onsubmit="return confirm('Move to Trash?')"><input type="hidden" name="id" value="{{ f.id }}"><button>♲</button></form>{% endif %}</div></article>{% else %}<div class="empty">☁<h3>No files here yet</h3><p>Upload a file or create a folder to get started.</p></div>{% endfor %}</div></main></div>
<div id="preview-modal" class="preview-modal" hidden role="dialog" aria-modal="true" aria-label="File preview">
  <div class="preview-panel">
    <button type="button" class="preview-close" id="preview-close">✕ Close</button>
    <img id="preview-image" alt="Image preview" hidden>
    <video id="preview-video" controls playsinline hidden></video>
    <audio id="preview-audio" controls hidden></audio>
    <iframe id="preview-document" title="Document preview" hidden></iframe>
    <pre id="preview-text" hidden></pre>
    <div id="preview-message" class="preview-message" hidden></div>
  </div>
</div>
<script>
(function(){
  const modal = document.getElementById('preview-modal');
  const image = document.getElementById('preview-image');
  const video = document.getElementById('preview-video');
  const audio = document.getElementById('preview-audio');
  const documentFrame = document.getElementById('preview-document');
  const textView = document.getElementById('preview-text');
  const messageView = document.getElementById('preview-message');
  const closeButton = document.getElementById('preview-close');

  function resetPlayers(){
    image.removeAttribute('src');
    video.pause();
    video.removeAttribute('src');
    video.load();
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    documentFrame.removeAttribute('src');
    textView.textContent = '';
    messageView.textContent = '';
    image.hidden = true;
    video.hidden = true;
    audio.hidden = true;
    documentFrame.hidden = true;
    textView.hidden = true;
    messageView.hidden = true;
  }

  function closePreview(){
    modal.hidden = true;
    resetPlayers();
  }

  document.addEventListener('click', function(event){
    const link = event.target.closest('a.preview-trigger');
    if(link){
      event.preventDefault();
      resetPlayers();

      const kind = link.dataset.kind || 'Images';
      const url = link.href;
      const name = link.dataset.name || link.textContent.trim() || 'File preview';

      if(kind === 'Videos'){
        video.src = url;
        video.hidden = false;
        video.load();
      } else if(kind === 'Audio'){
        audio.src = url;
        audio.hidden = false;
        audio.load();
      } else if (kind === 'Images') {
        image.src = url;
        image.alt = name;
        image.hidden = false;
      } else {
        const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
        const textExtensions = ['txt', 'csv', 'md', 'log', 'json', 'xml', 'html', 'htm', 'css', 'js', 'py', 'yaml', 'yml', 'ini', 'rtf'];
        const officeExtensions = ['docx', 'xlsx', 'pptx', 'odt', 'ods'];
        const notebookExtensions = ['ipynb'];
        const appleExtensions = ['pages', 'numbers', 'key'];

        if (ext === 'pdf') {
          documentFrame.src = url;
          documentFrame.hidden = false;
        } else if (textExtensions.includes(ext) || officeExtensions.includes(ext) || notebookExtensions.includes(ext)) {
          textView.textContent = 'Loading preview…';
          textView.hidden = false;
          const contentUrl = (officeExtensions.includes(ext) || notebookExtensions.includes(ext))
            ? url.replace('/preview/', '/preview-content/')
            : url;
          fetch(contentUrl)
            .then(function(response) {
              if (!response.ok) throw new Error('Could not load file');
              return response.text();
            })
            .then(function(content) {
              textView.textContent = content;
            })
            .catch(function() {
              textView.textContent = 'This text file could not be previewed. Please use the Download option.';
            });
        } else {
          if (appleExtensions.includes(ext)) {
            messageView.textContent = name + ' — Apple document format detected. Full preview requires a compatible renderer or conversion. Download the file to open it in Pages, Numbers, or Keynote.';
          } else {
            messageView.textContent = name + ' — this format needs a compatible document renderer to display its contents here. Use the Download option to open it in a suitable app.';
          }
          messageView.hidden = false;
        }
      }

      modal.hidden = false;
      closeButton.focus();
    } else if(event.target === modal){
      closePreview();
    }
  });

  document.querySelectorAll('video.video-thumb').forEach(function(thumb){
    const link = thumb.closest('a.preview-trigger');
    if(!link) return;

    thumb.addEventListener('loadedmetadata', function(){
      if(!Number.isFinite(thumb.duration) || thumb.duration <= 0) return;
      const safeEnd = Math.max(0, thumb.duration - 0.25);
      thumb.currentTime = Math.random() * safeEnd;
    }, { once: true });

    thumb.addEventListener('seeked', function(){
      thumb.pause();
    }, { once: true });

    thumb.addEventListener('error', function(){
      thumb.style.display = 'none';
    });

    thumb.src = thumb.dataset.src;
    thumb.load();
  });

  closeButton.addEventListener('click', closePreview);
  document.addEventListener('keydown', function(event){
    if(event.key === 'Escape' && !modal.hidden) closePreview();
  });
})();
</script>
<script>
(function(){
 var names=["theme-light","theme-warm-coral","theme-sky-high","theme-sunlit-gold","theme-earthy-tones","theme-twilight-blues","theme-vibrant-fern","theme-emerald-green","theme-celestial-night","theme-premium-black"];
 var body=document.body, buttons=document.querySelectorAll(".theme-option");
 var themeLabels={
  "theme-light":"Sunny Ivory",
  "theme-warm-coral":"Warm Corals and Oranges",
  "theme-sky-high":"Sky High",
  "theme-sunlit-gold":"Sunlit Gold",
  "theme-earthy-tones":"Earthy Tones",
  "theme-twilight-blues":"Twilight Blues",
  "theme-vibrant-fern":"Vibrant Fern Fronds",
  "theme-emerald-green":"Emerald Green",
  "theme-celestial-night":"Celestial Night",
  "theme-premium-black":"Premium Black"
 };
 
 var cycleButton=document.getElementById("theme-cycle-button");
 function syncThemeCycleSize(){
  var logoutButton=document.querySelector('header.top a[href="/logout"]');
  if(!cycleButton || !logoutButton) return;
  var r=logoutButton.getBoundingClientRect();
  if(r.width<=0 || r.height<=0) return;
  cycleButton.style.setProperty("width",r.width+"px","important");
  cycleButton.style.setProperty("min-width",r.width+"px","important");
  cycleButton.style.setProperty("height",r.height+"px","important");
  cycleButton.style.setProperty("flex-basis",r.width+"px","important");
 }
 var cycleArt=document.getElementById("theme-cycle-art");
 var artwork={
  "theme-light":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#fbf4e0"/><rect y="24" width="160" height="32" fill="#f6ecd0"/><circle cx="124" cy="18" r="15" fill="#efd9a0"/><circle cx="124" cy="18" r="9" fill="#d9b45a"/><g fill="#fff"><ellipse cx="38" cy="17" rx="14" ry="4"/><ellipse cx="48" cy="13" rx="8" ry="4"/><ellipse cx="88" cy="25" rx="10" ry="3"/></g><path d="M0 42Q40 28 80 40T160 36V56H0Z" fill="#e6d5aa"/><path d="M0 49Q50 38 100 48T160 45V56H0Z" fill="#cdb67c"/></svg>',
  "theme-warm-coral":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#ffd2b0"/><rect y="18" width="160" height="38" fill="#ffb08c"/><rect y="32" width="160" height="24" fill="#f58a68"/><g fill="#ffe3cf"><ellipse cx="30" cy="14" rx="14" ry="3.5"/><ellipse cx="128" cy="22" rx="12" ry="3"/></g><circle cx="80" cy="40" r="15" fill="#fff0d2"/><path d="M0 44Q40 34 80 43T160 40V56H0Z" fill="#c8553d"/><path d="M0 51Q60 44 160 51V56H0Z" fill="#6b2a20"/></svg>',
  "theme-sky-high":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#6bb8f0"/><rect y="28" width="160" height="28" fill="#a9dcfa"/><g fill="#fff"><circle cx="34" cy="22" r="7"/><circle cx="44" cy="19" r="9"/><circle cx="55" cy="23" r="6"/><rect x="28" y="22" width="33" height="7" rx="3.5"/><circle cx="122" cy="13" r="5"/><circle cx="130" cy="12" r="6"/><rect x="117" y="13" width="20" height="5" rx="2.5"/></g><path d="M88 30q3-3 6 0q3-3 6 0" fill="none" stroke="#1b3a6b" stroke-width="1.2" stroke-linecap="round"/><path d="M0 46Q40 38 90 46T160 42V56H0Z" fill="#dff4ff"/><path d="M0 52Q60 47 160 52V56H0Z" fill="#bfe6fa"/></svg>',
  "theme-sunlit-gold":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#fff2bd"/><rect y="26" width="160" height="30" fill="#fbe08a"/><g stroke="#e0a01a" stroke-width="2" stroke-linecap="round" transform="translate(118 22)"><path d="M0-19V-15"/><path d="M0-19V-15" transform="rotate(45)"/><path d="M0-19V-15" transform="rotate(90)"/><path d="M0-19V-15" transform="rotate(135)"/><path d="M0-19V-15" transform="rotate(180)"/><path d="M0-19V-15" transform="rotate(225)"/><path d="M0-19V-15" transform="rotate(270)"/><path d="M0-19V-15" transform="rotate(315)"/></g><circle cx="118" cy="22" r="11" fill="#f2b21c"/><path d="M0 44Q40 34 80 44T160 40V56H0Z" fill="#e0b04a"/><path d="M0 51Q60 45 160 51V56H0Z" fill="#b8812a"/></svg>',
  "theme-earthy-tones":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#f6dcb0"/><rect y="22" width="160" height="34" fill="#efcb94"/><circle cx="132" cy="15" r="8" fill="#fbeccc"/><path d="M0 40Q30 28 70 40T160 34V56H0Z" fill="#d9a566"/><path d="M0 48Q60 38 110 48T160 46V56H0Z" fill="#b9824a"/><g stroke="#5f7a45" stroke-width="3" stroke-linecap="round" fill="none"><path d="M26 51V32"/><path d="M26 43H21V37"/><path d="M26 40H31V35"/></g><g fill="#5a3a22" stroke="#5a3a22"><ellipse cx="90" cy="36" rx="11" ry="4.5" stroke="none"/><circle cx="86" cy="31" r="4" stroke="none"/><circle cx="94" cy="31.5" r="3.6" stroke="none"/><path d="M99 34Q104 30 103 24" fill="none" stroke-width="2.4" stroke-linecap="round"/><ellipse cx="105" cy="23" rx="3.4" ry="1.8" stroke="none" transform="rotate(-20 105 23)"/><path d="M82 39V47M86 39V47M94 39V47M98 39V47" fill="none" stroke-width="1.6" stroke-linecap="round"/></g></svg>',
  "theme-twilight-blues":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#3a4780"/><rect y="18" width="160" height="38" fill="#4b5a94"/><rect y="34" width="160" height="22" fill="#6a7cab"/><g fill="#dfe7ff"><circle cx="20" cy="10" r="1"/><circle cx="54" cy="16" r="1.2"/><circle cx="84" cy="8" r="1"/><circle cx="146" cy="30" r="1"/></g><path d="M122 9a7 7 0 1 0 6 11a6 6 0 0 1-6-11Z" fill="#eef2ff"/><path d="M0 42Q40 30 80 40T160 34V56H0Z" fill="#7a8dba"/><path d="M0 48Q50 40 100 48T160 44V56H0Z" fill="#46568a"/><path d="M0 53Q60 49 160 53V56H0Z" fill="#27336a"/></svg>',
  "theme-vibrant-fern":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><g id="fernFrond"><path d="M0 0Q6-22 22-40" fill="none" stroke="#2e7d32" stroke-width="1.5"/><g fill="#3f9a3a"><ellipse cx="-1.2" cy="-8.6" rx="4.5" ry="1.5" transform="rotate(25 -1.2 -8.6)"/><ellipse cx="7.3" cy="-10.6" rx="4.5" ry="1.5" transform="rotate(-25 7.3 -10.6)"/><ellipse cx="2.4" cy="-17" rx="4.5" ry="1.5" transform="rotate(25 2.4 -17)"/><ellipse cx="10.9" cy="-19" rx="4.5" ry="1.5" transform="rotate(-25 10.9 -19)"/><ellipse cx="6.8" cy="-25" rx="4.5" ry="1.5" transform="rotate(25 6.8 -25)"/><ellipse cx="15.3" cy="-27" rx="4.5" ry="1.5" transform="rotate(-25 15.3 -27)"/><ellipse cx="12" cy="-32.6" rx="4" ry="1.4" transform="rotate(25 12 -32.6)"/><ellipse cx="20.5" cy="-34.6" rx="4" ry="1.4" transform="rotate(-25 20.5 -34.6)"/><ellipse cx="22" cy="-41" rx="2.5" ry="1.2" transform="rotate(-55 22 -41)"/></g></g></defs><rect width="160" height="56" fill="#e4f5c9"/><rect y="30" width="160" height="26" fill="#cdeba6"/><circle cx="80" cy="14" r="8" fill="#f6ffd8"/><use href="#fernFrond" transform="translate(48 56) scale(.8)"/><use href="#fernFrond" transform="translate(24 56) scale(1.1)"/><use href="#fernFrond" transform="translate(112 56) scale(-.8 .8)"/><use href="#fernFrond" transform="translate(136 56) scale(-1.1 1.1)"/><path d="M0 51Q60 45 160 51V56H0Z" fill="#7cc04e"/></svg>',
  "theme-emerald-green":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#0c4a37"/><path d="M0 56L40 0" stroke="#17805c" stroke-width="6" opacity=".5"/><path d="M0 46Q40 38 80 46T160 44V56H0Z" fill="#083a2a"/><polygon points="99,8 123,8 135,20 135,36 123,48 99,48 87,36 87,20" fill="#1fae7a"/><polygon points="101,16 121,16 127,22 127,34 121,40 101,40 95,34 95,22" fill="#3fd39b"/><path d="M99 8L101 16M123 8L121 16M135 20L127 22M135 36L127 34M123 48L121 40M99 48L101 40M87 36L95 34M87 20L95 22" stroke="#b9f5dc" stroke-width=".8" fill="none" opacity=".7"/><polygon points="103,19 113,19 107,31 103,31" fill="#fff" opacity=".3"/><path d="M30 14l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" fill="#9af0cc"/><path d="M62 34l1.3 3.7 3.7 1.3-3.7 1.3-1.3 3.7-1.3-3.7-3.7-1.3 3.7-1.3z" fill="#9af0cc"/></svg>',
  "theme-celestial-night":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#0d1245"/><ellipse cx="40" cy="36" rx="46" ry="16" fill="#5b3fc4" opacity=".55"/><ellipse cx="62" cy="30" rx="30" ry="9" fill="#2d6fd6" opacity=".4"/><g fill="#fff"><circle cx="14" cy="10" r="1"/><circle cx="30" cy="22" r="1.2"/><circle cx="52" cy="9" r="1"/><circle cx="76" cy="18" r="1.3"/><circle cx="92" cy="44" r="1"/><circle cx="104" cy="8" r="1"/><circle cx="148" cy="14" r="1.2"/><circle cx="150" cy="46" r="1"/><circle cx="68" cy="48" r="1"/></g><ellipse cx="120" cy="30" rx="18" ry="4.5" transform="rotate(-20 120 30)" fill="none" stroke="#8f9bff" stroke-width="1.5" opacity=".6"/><circle cx="120" cy="30" r="10" fill="#6a73e8"/><path d="M102 30A18 4.5 0 0 0 138 30" transform="rotate(-20 120 30)" fill="none" stroke="#cdd6ff" stroke-width="1.5"/></svg>',
  "theme-premium-black":'<svg viewBox="0 0 160 56" preserveAspectRatio="xMidY slice" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><rect width="160" height="56" fill="#0b0b0d"/><ellipse cx="90" cy="38" rx="64" ry="14" fill="#0f4f55" opacity=".4"/><rect y="45" width="160" height="11" fill="#14161a"/><rect y="45" width="160" height=".8" fill="#d8bf8a"/><path d="M6 28H30M2 34H24M8 40H34" stroke="#c9ced6" stroke-width="1" stroke-linecap="round" opacity=".45"/><polygon points="48,38 52,31 72,30 82,25 92,28 100,31 126,35 128,38 108,40 58,41" fill="#2b2e34"/><polygon points="52,31 56,26 72,29 72,30" fill="#2b2e34"/><path d="M60 33.5L98 34" stroke="#0f6b72" stroke-width="1.2"/><path d="M62 37L110 38" stroke="#d8bf8a" stroke-width="1"/><path d="M82 25L85 20H91L92 28Z" fill="#1a1c20"/><circle cx="87" cy="23" r="2.6" fill="#d8bf8a"/><path d="M80 25Q86 16 94 27" fill="none" stroke="#c9ced6" stroke-width="1"/><rect x="38" y="23" width="12" height="2.4" fill="#d8bf8a"/><rect x="44" y="25" width="1.6" height="13" fill="#1a1c20"/><rect x="38" y="21" width="1.6" height="8" fill="#1a1c20"/><rect x="116" y="39" width="16" height="2.4" fill="#d8bf8a"/><rect x="131" y="36" width="1.6" height="7" fill="#1a1c20"/><g fill="#0b0b0d" stroke="#c9ced6" stroke-width="1.2"><circle cx="56" cy="37" r="8"/><circle cx="108" cy="38" r="7"/></g><g fill="#1a1c20" stroke="#d8bf8a" stroke-width=".8"><circle cx="56" cy="37" r="3.4"/><circle cx="108" cy="38" r="3"/></g></svg>'
 };
 function updateCycleArt(name){
  if(!cycleArt) return;
  cycleArt.innerHTML=artwork[name]||artwork["theme-light"];
 }


 function apply(name){
  if(names.indexOf(name)<0) name="theme-light";
  names.forEach(function(n){body.classList.remove(n)});
  body.classList.add(name);
  buttons.forEach(function(b){b.setAttribute("aria-pressed",String(b.dataset.theme===name))});
  
  updateCycleArt(name);
  try{localStorage.setItem("nestdrive-theme",name)}catch(e){}
 }
 try{apply(localStorage.getItem("nestdrive-theme")||"theme-light")}catch(e){apply("theme-light")}
 buttons.forEach(function(b){b.addEventListener("click",function(){apply(b.dataset.theme)})});
 if(cycleButton){
  syncThemeCycleSize();
  window.addEventListener("load",syncThemeCycleSize);
  window.addEventListener("resize",syncThemeCycleSize);
  cycleButton.addEventListener("click",function(){
   var current=names.findIndex(function(n){return body.classList.contains(n)});
   apply(names[(current+1)%names.length]);
  });
 }

 var side=document.getElementById("sidebar");
 document.getElementById("menu-open").addEventListener("click",function(){side.classList.toggle("sidebar-open")});

})();
</script></body></html>"""

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        con.execute("""CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            name TEXT NOT NULL, relative_path TEXT NOT NULL, content_hash TEXT,
            size INTEGER NOT NULL DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0,
            is_folder INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id), UNIQUE(user_id, relative_path, is_deleted))""")
        # Upgrade the earlier setup_db.py schema safely in place.
        columns = {row[1] for row in con.execute("PRAGMA table_info(files)").fetchall()}
        if "is_folder" not in columns:
            con.execute("ALTER TABLE files ADD COLUMN is_folder INTEGER NOT NULL DEFAULT 0")
        if "deleted_at" not in columns:
            con.execute("ALTER TABLE files ADD COLUMN deleted_at TEXT")
        # Start a fresh 30-day window for existing Trash entries.
        con.execute("UPDATE files SET deleted_at=CURRENT_TIMESTAMP WHERE is_deleted=1 AND deleted_at IS NULL")
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id,is_deleted)")
        con.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")

def cleanup_expired_trash():
    """Purge Trash records older than 30 days without deleting shared blobs."""
    with db() as con:
        rows = con.execute("""SELECT id, content_hash FROM files
            WHERE is_deleted=1 AND deleted_at IS NOT NULL
              AND deleted_at <= datetime('now','-30 days')""").fetchall()
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        hashes = {r["content_hash"] for r in rows if r["content_hash"]}
        marks = ",".join("?" for _ in ids)
        con.execute(f"DELETE FROM files WHERE id IN ({marks})", ids)
        for digest in hashes:
            refs = con.execute("SELECT COUNT(*) FROM files WHERE content_hash=?", (digest,)).fetchone()[0]
            if refs == 0 and re.fullmatch(r"[0-9a-f]{64}", digest):
                object_path(digest).unlink(missing_ok=True)
        return len(rows)

def user_id():
    return session.get("user_id")

def protected(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if not user_id():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return inner

def user_rows(include_deleted=False):
    with db() as con:
        if include_deleted:
            return con.execute("SELECT * FROM files WHERE user_id=? ORDER BY is_folder DESC,name COLLATE NOCASE", (user_id(),)).fetchall()
        return con.execute("SELECT * FROM files WHERE user_id=? AND is_deleted=0 ORDER BY is_folder DESC,name COLLATE NOCASE", (user_id(),)).fetchall()

def logical_path(parent, name):
    name = secure_filename(name)
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        abort(400, "Invalid name.")
    rel = str(PurePosixPath(parent) / name) if parent else name
    if rel.startswith("/") or ".." in PurePosixPath(rel).parts:
        abort(400, "Invalid path.")
    return rel

def object_path(digest):
    if not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
        abort(404)
    return OBJECTS / digest[:2] / digest[2:]

def store_stream(stream):
    h = hashlib.sha256()
    temp = OBJECTS / (".tmp-" + os.urandom(8).hex())
    size = 0
    try:
        with open(temp, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
                out.write(chunk)
        digest = h.hexdigest()
        target = object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            temp.unlink(missing_ok=True)
        else:
            os.replace(temp, target)
        return digest, size
    finally:
        temp.unlink(missing_ok=True)

def add_file(uid, rel, digest, size, deleted=0):
    name = PurePosixPath(rel).name
    with db() as con:
        con.execute("""INSERT INTO files(user_id,name,relative_path,content_hash,size,is_deleted,is_folder)
                       VALUES(?,?,?,?,?,?,0)""", (uid, name, rel, digest, size, deleted))

def migrate_legacy_once():
    """Copy existing root uploads into user1 records once; never remove originals."""
    with db() as con:
        done = con.execute("SELECT value FROM settings WHERE key='legacy_migrated'").fetchone()
        first = con.execute("SELECT id FROM users WHERE username='user1' COLLATE NOCASE").fetchone()
        if done:
            return
        if not first:
            # Do not mark migration done until user1 exists.
            return
        uid = first["id"]
    # Walk existing files, excluding internal folders. Keep source files untouched.
    for p in sorted(STORAGE.iterdir(), key=lambda x: x.name.lower()):
        if p.name.startswith(".") or p.name == "objects":
            continue
        if p.is_file():
            try:
                with db() as con:
                    exists = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=?", (uid, p.name)).fetchone()
                if exists:
                    continue
                with p.open("rb") as src:
                    digest, size = store_stream(src)
                add_file(uid, p.name, digest, size)
            except (OSError, sqlite3.IntegrityError):
                continue
        elif p.is_dir():
            for folder, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                relfolder = Path(folder).relative_to(STORAGE).as_posix()
                with db() as con:
                    try:
                        con.execute("INSERT OR IGNORE INTO files(user_id,name,relative_path,size,is_deleted,is_folder) VALUES(?,?,?,?,0,1)",
                                    (uid, Path(folder).name, relfolder, 0))
                    except sqlite3.Error:
                        pass
                for fname in files:
                    fp = Path(folder) / fname
                    rel = fp.relative_to(STORAGE).as_posix()
                    try:
                        with db() as con:
                            exists = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=?", (uid, rel)).fetchone()
                        if exists:
                            continue
                        with fp.open("rb") as src:
                            digest, size = store_stream(src)
                        add_file(uid, rel, digest, size)
                    except (OSError, sqlite3.IntegrityError):
                        continue
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('legacy_migrated','1')")

def row_by_id(fid, include_deleted=False):
    with db() as con:
        q = "SELECT * FROM files WHERE id=? AND user_id=?"
        args = [fid, user_id()]
        if not include_deleted:
            q += " AND is_deleted=0"
        row = con.execute(q, args).fetchone()
    if not row:
        abort(404)
    return row

def kind_name(name, folder=False):
    if folder:
        return "Folders"
    ext = Path(name).suffix.lower()
    for label, exts in TYPES.items():
        if ext in exts:
            return label
    return "Other"

def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

def list_records(category, query):
    rows = user_rows(include_deleted=(category == "Trash"))
    records = []
    for r in rows:
        if category == "Trash" and not r["is_deleted"]:
            continue
        if category != "Trash" and r["is_deleted"]:
            continue
        # The home page lists only root-level items.
        # Nested items appear when their parent folder is opened.
        if category != "Trash" and "/" in r["relative_path"].strip("/"):
            continue
        k = "Trash" if r["is_deleted"] else kind_name(r["name"], bool(r["is_folder"]))
        if category not in ("All files", "Trash") and k != category:
            continue
        if query.casefold() not in r["name"].casefold():
            continue
        records.append({"id": r["id"], "name": r["name"], "path": r["relative_path"],
                        "kind": k, "size": "Folder" if r["is_folder"] else fmt_size(r["size"]),
                        "icon": ""})
    return records

def insert_folder(uid, rel):
    parent = str(PurePosixPath(rel).parent)
    if parent == ".": parent = ""
    name = PurePosixPath(rel).name
    with db() as con:
        exists = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=? AND is_deleted=0", (uid, rel)).fetchone()
        if exists:
            abort(409, "That name already exists.")
        con.execute("INSERT INTO files(user_id,name,relative_path,size,is_deleted,is_folder) VALUES(?,?,?,?,0,1)",
                    (uid, name, rel, 0))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            row = con.execute("SELECT id,username,password_hash FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            return redirect("/")
        flash("Incorrect username or password.")
    return render_template_string(LOGIN_TEMPLATE, register=False, subtitle="Sign in to your private cloud.")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            flash("Username must be 3–32 characters: letters, numbers, dot, underscore or hyphen.")
        elif len(password) < 8:
            flash("Choose a password with at least 8 characters.")
        elif password != confirm:
            flash("Passwords do not match.")
        else:
            try:
                with db() as con:
                    con.execute("INSERT INTO users(username,password_hash) VALUES(?,?)",
                                (username, generate_password_hash(password, method=PASSWORD_METHOD)))
                flash("Account created. Please sign in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username already exists.")
    return render_template_string(LOGIN_TEMPLATE, register=True, subtitle="Create a new private account.")

@app.route("/")
@protected
def home():
    category = request.args.get("category", "All files")
    allowed = ["All files", "Folders", *TYPES.keys(), "Other", "Trash"]
    if category not in allowed: category = "All files"
    query = request.args.get("q", "")
    layout = request.args.get("layout", "grid")
    if layout not in ("grid", "list"):
        layout = "grid"
    cleanup_expired_trash()
    records = list_records(category, query)
    counts = {c: len(list_records(c, "")) for c in ["Folders", *TYPES.keys(), "Other"]}
    used = sum(r["size"] for r in user_rows() if not r["is_folder"])
    return render_template_string(HOME_TEMPLATE, shown=records, selected=category, query=query,
                                  layout=layout, counts=counts, used=fmt_size(used), username=session.get("username","user"), is_folder=False)

@app.route("/upload", methods=["POST"])
@protected
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400, "Choose a file first.")
    name = secure_filename(Path(f.filename.replace("\\", "/")).name)
    if not name:
        abort(400, "Invalid filename.")
    parent = request.form.get("parent", "").strip("/")
    if parent:
        with db() as con:
            parent_row = con.execute(
                "SELECT id FROM files WHERE user_id=? AND relative_path=? AND is_folder=1 AND is_deleted=0",
                (user_id(), parent)
            ).fetchone()
        if not parent_row:
            abort(400, "Destination folder not found.")
    rel_name = str(PurePosixPath(parent) / name) if parent else name
    with db() as con:
        exists = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=? AND is_deleted=0", (user_id(), rel_name)).fetchone()
    if exists: abort(409, "That name already exists. Rename it and try again.")
    if name.lower().endswith(".zip") and not parent:
        try:
            data = f.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                members = []
                for info in z.infolist():
                    raw = info.filename.replace("\\", "/")
                    pp = PurePosixPath(raw)
                    if pp.is_absolute() or ".." in pp.parts or not raw or raw.startswith("/"):
                        abort(400, "Unsafe ZIP contents.")
                    members.append((info, pp))
                root = secure_filename(Path(name).stem)
                if not root: abort(400, "Invalid ZIP filename.")
                insert_folder(user_id(), root)
                for info, pp in members:
                    if info.is_dir():
                        rel = str(PurePosixPath(root) / pp)
                        if rel != root:
                            # Create intermediate folders safely.
                            parts = PurePosixPath(rel).parts
                            for i in range(2, len(parts)+1):
                                sub = "/".join(parts[:i])
                                with db() as con:
                                    found = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=? AND is_deleted=0", (user_id(), sub)).fetchone()
                                if not found: insert_folder(user_id(), sub)
                        continue
                    rel = str(PurePosixPath(root) / pp)
                    parts = PurePosixPath(rel).parts
                    for i in range(2, len(parts)):
                        sub = "/".join(parts[:i])
                        with db() as con:
                            found = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=? AND is_deleted=0", (user_id(), sub)).fetchone()
                        if not found: insert_folder(user_id(), sub)
                    with db() as con:
                        found = con.execute("SELECT 1 FROM files WHERE user_id=? AND relative_path=? AND is_deleted=0", (user_id(), rel)).fetchone()
                    if found: continue
                    digest, size = store_stream(io.BytesIO(z.read(info)))
                    add_file(user_id(), rel, digest, size)
        except zipfile.BadZipFile:
            abort(400, "This is not a valid ZIP file.")
    else:
        digest, size = store_stream(f.stream)
        add_file(user_id(), rel_name, digest, size)
    flash("Upload complete.")
    return redirect(url_for("folder", folder_path=parent) if parent else "/")

@app.route("/mkdir", methods=["POST"])
@protected
def mkdir():
    name = request.form.get("name", "")
    parent = request.form.get("parent", "").strip("/")
    if parent:
        with db() as con:
            parent_row = con.execute(
                "SELECT id FROM files WHERE user_id=? AND relative_path=? AND is_folder=1 AND is_deleted=0",
                (user_id(), parent)
            ).fetchone()
        if not parent_row:
            abort(400, "Destination folder not found.")
    rel = logical_path(parent, name)
    insert_folder(user_id(), rel)
    return redirect(url_for("folder", folder_path=parent) if parent else "/")

@app.route("/download/<int:fid>")
@protected
def download(fid):
    row = row_by_id(fid)
    if row["is_folder"] or not row["content_hash"]:
        abort(404)
    path = object_path(row["content_hash"])
    if not path.is_file(): abort(404, "Stored file object is missing.")
    return send_file(path, as_attachment=True, download_name=row["name"])

@app.route("/download-folder/<path:folder_path>")
@protected
def download_folder(folder_path):
    folder_path = folder_path.strip("/")
    if not folder_path or any(part in {"", ".", ".."} for part in folder_path.split("/")):
        abort(400, "Invalid folder path.")

    safe_parts = [secure_filename(part) for part in folder_path.split("/")]
    if any(not part for part in safe_parts):
        abort(400, "Invalid folder path.")
    folder_path = "/".join(safe_parts)
    prefix = folder_path + "/"

    with db() as con:
        folder_row = con.execute(
            "SELECT id FROM files WHERE user_id=? AND relative_path=? "
            "AND is_folder=1 AND is_deleted=0",
            (user_id(), folder_path)
        ).fetchone()
        if not folder_row:
            abort(404)

        rows = con.execute(
            "SELECT name, relative_path, is_folder, content_hash FROM files "
            "WHERE user_id=? AND is_deleted=0 AND relative_path LIKE ? "
            "ORDER BY relative_path",
            (user_id(), prefix + "%")
        ).fetchall()

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            rel_path = row["relative_path"]
            if not rel_path.startswith(prefix):
                continue
            if row["is_folder"] or not row["content_hash"]:
                continue

            source = object_path(row["content_hash"])
            if not source.is_file():
                continue

            archive_name = rel_path[len(prefix):]
            if archive_name:
                archive.write(source, archive_name)

    memory_file.seek(0)
    zip_name = folder_path.rsplit("/", 1)[-1] + ".zip"
    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=zip_name,
    )




def extract_office_preview(path, ext):
    """Extract readable text from modern Office/OpenDocument ZIP files."""
    output = []
    max_output = 1_500_000
    max_xml_file = 20_000_000

    def xml_root(archive, member):
        info = archive.getinfo(member)
        if info.file_size > max_xml_file:
            raise ValueError("Document XML part is too large")
        return ET.fromstring(archive.read(member))

    def local_name(tag):
        return tag.rsplit("}", 1)[-1]

    def text_of(element):
        return "".join(
            node.text or ""
            for node in element.iter()
            if local_name(node.tag) == "t"
        )

    def add(value):
        if value:
            output.append(value)
        if sum(map(len, output)) > max_output:
            raise ValueError("Extracted preview is too large")

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())

        if ext == ".docx":
            root = xml_root(archive, "word/document.xml")
            body = next(
                (node for node in root.iter()
                 if local_name(node.tag) == "body"),
                root
            )
            for node in body:
                kind = local_name(node.tag)
                if kind == "p":
                    add(text_of(node))
                elif kind == "tbl":
                    add("\n[TABLE]")
                    for row in node:
                        if local_name(row.tag) == "tr":
                            cells = []
                            for cell in row:
                                if local_name(cell.tag) == "tc":
                                    cells.append(text_of(cell))
                            add(" | ".join(cells))
                    add("[/TABLE]")
                if sum(map(len, output)) > max_output:
                    break

        elif ext == ".pptx":
            slides = []
            for name in names:
                match = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
                if match:
                    slides.append((int(match.group(1)), name))
            for number, name in sorted(slides):
                add(f"\n--- Slide {number} ---")
                root = xml_root(archive, name)
                for paragraph in root.iter():
                    if local_name(paragraph.tag) == "p":
                        value = "".join(
                            node.text or ""
                            for node in paragraph.iter()
                            if local_name(node.tag) == "t"
                        )
                        add(value)
                if sum(map(len, output)) > max_output:
                    break

        elif ext == ".xlsx":
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = xml_root(archive, "xl/sharedStrings.xml")
                for item in root:
                    if local_name(item.tag) == "si":
                        shared.append(text_of(item))

            sheets = []
            for name in names:
                match = re.fullmatch(
                    r"xl/worksheets/sheet(\d+)\.xml", name
                )
                if match:
                    sheets.append((int(match.group(1)), name))

            for number, name in sorted(sheets):
                add(f"\n--- Worksheet {number} ---")
                root = xml_root(archive, name)
                for row in root.iter():
                    if local_name(row.tag) != "row":
                        continue
                    cells = []
                    for cell in row:
                        if local_name(cell.tag) != "c":
                            continue
                        ref = cell.attrib.get("r", "")
                        value = ""
                        for child in cell:
                            kind = local_name(child.tag)
                            if kind == "v":
                                value = child.text or ""
                            elif kind == "is":
                                value = text_of(child)
                        if cell.attrib.get("t") == "s" and value:
                            try:
                                value = shared[int(value)]
                            except (ValueError, IndexError):
                                pass
                        if value:
                            cells.append(f"{ref}={value}" if ref else value)
                    if cells:
                        add(" | ".join(cells))
                if sum(map(len, output)) > max_output:
                    break

        elif ext in {".odt", ".ods"}:
            if "content.xml" not in names:
                raise ValueError("OpenDocument content not found")
            root = xml_root(archive, "content.xml")
            for node in root.iter():
                kind = local_name(node.tag)
                if kind in {"p", "h"}:
                    value = "".join(node.itertext()).strip()
                    add(value)
                elif ext == ".ods" and kind == "table-row":
                    cells = []
                    for cell in node:
                        if local_name(cell.tag) == "table-cell":
                            value = "".join(cell.itertext()).strip()
                            if value:
                                cells.append(value)
                    if cells:
                        add(" | ".join(cells))
                if sum(map(len, output)) > max_output:
                    break

        else:
            raise ValueError("Unsupported document format")

    result = "\n".join(output).strip()
    if not result:
        return "No extractable text was found in this file."
    return result[:max_output]


@app.route("/preview-content/<int:fid>")
@protected
def preview_content(fid):
    row = row_by_id(fid)
    if row["is_folder"] or not row["content_hash"]:
        abort(404)

    ext = Path(row["name"]).suffix.lower()
    allowed = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".ipynb"}
    if ext not in allowed:
        abort(415)

    path = object_path(row["content_hash"])
    if not path.is_file():
        abort(404)

    try:
        if ext == ".ipynb":
            notebook = json.loads(path.read_text(encoding="utf-8"))
            parts = []
            for index, cell in enumerate(notebook.get("cells", []), 1):
                cell_type = cell.get("cell_type", "cell")
                source = cell.get("source", "")
                if isinstance(source, list):
                    source = "".join(source)
                parts.append(f"--- Cell {index} ({cell_type}) ---\n{source}")

                for output in cell.get("outputs", []):
                    output_text = output.get("text", "")
                    if isinstance(output_text, list):
                        output_text = "".join(output_text)
                    data = output.get("data", {})
                    if not output_text and "text/plain" in data:
                        output_text = data["text/plain"]
                        if isinstance(output_text, list):
                            output_text = "".join(output_text)
                    if output_text:
                        parts.append("Output:\n" + str(output_text))

            content = "\n\n".join(parts)[:1_500_000]
        else:
            content = extract_office_preview(path, ext)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError,
            zipfile.BadZipFile, ET.ParseError):
        abort(422, "This document could not be parsed for text preview.")

    return send_file(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/plain; charset=utf-8",
        as_attachment=False,
        download_name=Path(row["name"]).stem + ".txt",
    )


@app.route("/preview/<int:fid>")
@protected
def preview(fid):
    row = row_by_id(fid)
    if row["is_folder"] or not row["content_hash"]: abort(404)
    path = object_path(row["content_hash"])
    if not path.is_file(): abort(404)
    mime_type, _ = mimetypes.guess_type(row["name"])
    return send_file(
        path,
        mimetype=mime_type or "application/octet-stream",
        as_attachment=False,
        download_name=row["name"],
    )

@app.route("/folder/<path:folder_path>")
@protected
def folder(folder_path):
    # Validate a nested path component-by-component; don't sanitize the
    # entire path as one filename, because that destroys its separators.
    parts = folder_path.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        abort(400, "Invalid folder path.")
    safe_parts = [secure_filename(part) for part in parts]
    if any(not part for part in safe_parts):
        abort(400, "Invalid folder path.")
    folder_path = "/".join(safe_parts)
    with db() as con:
        row = con.execute(
            "SELECT * FROM files WHERE user_id=? AND relative_path=? "
            "AND is_folder=1 AND is_deleted=0",
            (user_id(), folder_path)
        ).fetchone()
        if not row:
            abort(404)

        prefix = folder_path.rstrip("/") + "/"
        rows = con.execute(
            "SELECT * FROM files WHERE user_id=? AND is_deleted=0 "
            "AND relative_path LIKE ? ORDER BY relative_path",
            (user_id(), prefix + "%")
        ).fetchall()

    items = []
    for r in rows:
        rel = r["relative_path"]
        if not rel.startswith(prefix):
            continue
        remainder = rel[len(prefix):]
        if not remainder or "/" in remainder:
            continue
        items.append({
            "name": r["name"],
            "id": r["id"],
            "folder": bool(r["is_folder"]),
            "kind": kind_name(r["name"], bool(r["is_folder"])),
            "relative_path": rel,
            "size": r["size"],
        })

    parent = str(PurePosixPath(folder_path).parent)
    if parent == ".":
        parent = ""

    shown = []
    for item in items:
        is_subfolder = item["folder"]
        shown.append({
            "name": item["name"],
            "id": item["id"],
            "kind": "Folders" if is_subfolder else item["kind"],
            "path": item["relative_path"],
            "size": "Folder" if is_subfolder else fmt_size(item["size"]),
            "icon": "",
        })

    counts = {
        c: len(list_records(c, ""))
        for c in ["Folders", *TYPES.keys(), "Other"]
    }
    used = sum(r["size"] for r in user_rows() if not r["is_folder"])

    return render_template_string(
        HOME_TEMPLATE,
        shown=shown,
        selected="Folder",
        query="",
        layout="grid",
        counts=counts,
        used=fmt_size(used),
        username=session.get("username", "user"),
        is_folder=True,
        folder_name=PurePosixPath(folder_path).name,
        folder_path=folder_path,
        parent=parent,
    )

@app.route("/rename", methods=["POST"])
@protected
def rename():
    fid = request.form.get("id", type=int)
    newname = secure_filename(request.form.get("new", ""))
    if not newname or newname in {".", ".."}:
        abort(400, "Invalid name.")

    row = row_by_id(fid)
    oldrel = row["relative_path"]
    parent = str(PurePosixPath(oldrel).parent)
    if parent == ".":
        parent = ""
    newrel = str(PurePosixPath(parent) / newname) if parent else newname

    if newrel == oldrel:
        return redirect("/")

    if not row["is_folder"]:
        old_suffix = Path(row["name"]).suffix
        if old_suffix:
            candidate = Path(newname)
            if candidate.suffix.lower() != old_suffix.lower():
                newname = candidate.stem + old_suffix
            if not newname or newname == old_suffix:
                abort(400, "Enter a valid filename.")
            newrel = str(PurePosixPath(parent) / newname) if parent else newname

    with db() as con:
        if row["is_folder"]:
            # Prevent moving a folder inside itself if route semantics change later.
            prefix = oldrel.rstrip("/") + "/"
            descendants = con.execute(
                "SELECT id, relative_path FROM files "
                "WHERE user_id=? AND relative_path LIKE ? AND is_deleted=?",
                (user_id(), prefix + "%", row["is_deleted"])
            ).fetchall()
            subtree = [(row["id"], oldrel)] + [
                (r["id"], r["relative_path"]) for r in descendants
            ]
        else:
            subtree = [(row["id"], oldrel)]

        ids = {item_id for item_id, _ in subtree}
        marks = ",".join("?" for _ in ids)
        existing = con.execute(
            f"SELECT id, relative_path FROM files WHERE user_id=? "
            f"AND is_deleted=? AND id NOT IN ({marks})",
            [user_id(), row["is_deleted"], *ids]
        ).fetchall()

        new_paths = {}
        for item_id, old_path in subtree:
            suffix = old_path[len(oldrel):]
            target = newrel + suffix
            new_paths[item_id] = target

        occupied = {r["relative_path"] for r in existing}
        if any(path in occupied for path in new_paths.values()):
            abort(409, "That name conflicts with an existing file or folder.")

        # Move all affected paths aside first to avoid UNIQUE constraint clashes.
        import uuid
        token = uuid.uuid4().hex
        for item_id, old_path in subtree:
            con.execute(
                "UPDATE files SET relative_path=? WHERE id=? AND user_id=?",
                (f".rename-{token}-{item_id}", item_id, user_id())
            )

        for item_id, target in new_paths.items():
            if item_id == row["id"]:
                con.execute(
                    "UPDATE files SET name=?, relative_path=? WHERE id=? AND user_id=?",
                    (newname, target, item_id, user_id())
                )
            else:
                con.execute(
                    "UPDATE files SET relative_path=? WHERE id=? AND user_id=?",
                    (target, item_id, user_id())
                )

    return redirect("/")


@app.route("/delete", methods=["POST"])
@protected
def delete():
    fid = request.form.get("id", type=int)
    row = row_by_id(fid)
    with db() as con:
        con.execute("UPDATE files SET is_deleted=1, deleted_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                    (fid, user_id()))
        if row["is_folder"]:
            con.execute("""UPDATE files SET is_deleted=1, deleted_at=CURRENT_TIMESTAMP
                           WHERE user_id=? AND relative_path LIKE ?""",
                        (user_id(), row["relative_path"].rstrip("/") + "/%"))
    return redirect("/")

@app.route("/restore", methods=["POST"])
@protected
def restore():
    fid = request.form.get("id", type=int)
    row = row_by_id(fid, include_deleted=True)
    with db() as con:
        con.execute("UPDATE files SET is_deleted=0, deleted_at=NULL WHERE id=? AND user_id=?",
                    (fid, user_id()))
        if row["is_folder"]:
            con.execute("""UPDATE files SET is_deleted=0, deleted_at=NULL
                           WHERE user_id=? AND relative_path LIKE ?""",
                        (user_id(), row["relative_path"].rstrip("/") + "/%"))
    return redirect("/?category=Trash")

@app.route("/purge", methods=["POST"])
@protected
def purge():
    fid = request.form.get("id", type=int)
    row = row_by_id(fid, include_deleted=True)
    if not row["is_deleted"]:
        abort(400, "Only items in Trash can be permanently deleted.")

    with db() as con:
        if row["is_folder"]:
            affected = con.execute("""SELECT id, content_hash FROM files
                WHERE user_id=? AND is_deleted=1
                  AND (relative_path=? OR relative_path LIKE ?)""",
                (user_id(), row["relative_path"], row["relative_path"].rstrip("/") + "/%")).fetchall()
        else:
            affected = con.execute("SELECT id, content_hash FROM files WHERE id=? AND user_id=? AND is_deleted=1",
                                   (fid, user_id())).fetchall()

        ids = [r["id"] for r in affected]
        hashes = {r["content_hash"] for r in affected if r["content_hash"]}
        if ids:
            marks = ",".join("?" for _ in ids)
            con.execute(f"DELETE FROM files WHERE id IN ({marks})", ids)

        # Preserve any blob still referenced by any remaining account or file.
        for digest in hashes:
            refs = con.execute("SELECT COUNT(*) FROM files WHERE content_hash=?", (digest,)).fetchone()[0]
            if refs == 0 and re.fullmatch(r"[0-9a-f]{64}", digest):
                object_path(digest).unlink(missing_ok=True)
    return redirect("/?category=Trash")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return "Upload exceeds the configured 5 GB limit.", 413

@app.errorhandler(413)
def too_large_413(error):
    return "Upload exceeds the configured 5 GB limit.", 413

init_db()
# migrate_legacy_once()  # Disabled for portable distribution
cleanup_expired_trash()

if __name__ == "__main__":
    app.run(host=os.environ.get("NESTDRIVE_HOST", "127.0.0.1"), port=int(os.environ.get("NESTDRIVE_PORT", "8081")))

