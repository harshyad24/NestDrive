# NestDrive

NestDrive turns an Android phone (through Termux), a Mac or a Windows PC into a small private cloud drive on your local network. You open it in any web browser to upload, organise, preview and download your files. Nothing leaves your network, and there is no outside account or subscription.

## Features

- Accounts with hashed passwords; each user only sees their own files
- Upload files, or upload a `.zip` to have it unpacked into a folder
- Create folders, rename, move to Trash, restore, and delete permanently
- Trash is emptied automatically after 30 days
- Download single files, or a whole folder as a `.zip`
- In-browser preview for images, video, audio, PDF and text, plus text previews of `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods` and `.ipynb`
- Category filters (Images, Videos, Audio, Documents, Other), search, and grid or list view
- Storage-usage display
- 10 colour themes, remembered per browser
- Identical files are stored only once (deduplicated by SHA-256)
- Upload limit: 5 GB per request

## Requirements

- Python 3.9 or newer
- Flask 3.1 (installed automatically by the start scripts)

## Quick start

On every platform the steps are the same: run the **start** script, open the address it prints, create an account, and sign in.

### macOS

```bash
cd ~/Downloads/NestDrive        # or wherever you put the folder
./start_mac.sh
```

Open http://127.0.0.1:8081/ in your browser.

- If Python is missing, install it with `xcode-select --install` or from https://www.python.org/downloads/.
- If you get "permission denied", run `chmod +x start_mac.sh create_user_mac.sh` once.

### Windows

1. Install Python 3 from https://www.python.org/downloads/ (tick "Add Python to PATH").
2. Double-click `start_windows.bat`.
3. Open http://127.0.0.1:8081/ in your browser.

### Android (Termux)

```bash
pkg update && pkg install python
cd ~/NestDrive                  # wherever you copied the folder
bash start_termux.sh
```

Open http://127.0.0.1:8081/ in your phone's browser.

## Stopping the server

Press **Ctrl+C** in the terminal window that is running it. On Windows you can also just close the window.

If you lost the window on a Mac or in Termux, run:

```bash
pkill -f nestdrive_complete.py
```

## Creating accounts

There are two ways:

1. **In the browser:** click *Register* on the sign-in page.
2. **From the command line** (the server can be stopped or running):

| Platform | Command |
|---|---|
| macOS | `./create_user_mac.sh` |
| Windows | double-click `create_user_windows.bat` |
| Termux | `bash create_user_termux.sh` |

Run the start script at least once first, so the environment exists.

## Using it from other devices (phone as cloud storage)

By default NestDrive only accepts connections from the same device (`127.0.0.1`). To reach it from other devices on your Wi-Fi:

1. Start it listening on all network interfaces:

   | Platform | Command |
   |---|---|
   | macOS | `NESTDRIVE_HOST=0.0.0.0 ./start_mac.sh` |
   | Termux | `NESTDRIVE_HOST=0.0.0.0 python -u nestdrive_complete.py` |
   | Windows | `set NESTDRIVE_HOST=0.0.0.0` then `start_windows.bat` (both in the same Command Prompt) |

2. Find the host device's local IP address:
   - Termux: `ifconfig` (look for `wlan0`, for example `192.168.1.5`)
   - macOS: `ipconfig getifaddr en0`
   - Windows: `ipconfig` (IPv4 Address)
3. On the other device, open `http://<that-ip>:8081/`.

macOS and Windows may ask whether to allow incoming connections. Choose **Allow**. Only do this on a network you trust; see [Security](#security).

## Configuration

Settings are environment variables, set before you run the start script.

| Variable | Default | Purpose |
|---|---|---|
| `NESTDRIVE_HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` for LAN access. |
| `NESTDRIVE_PORT` | `8081` | Port number |
| `NESTDRIVE_SECRET` | auto-generated | Session signing key; overrides `.nestdrive_secret` |

## Project files

| File | Purpose |
|---|---|
| `nestdrive_complete.py` | The application: web server, routes, page templates and styling |
| `auth_config.py` | Paths, secret key loading, password-hash method |
| `setup_db.py` | Creates the database tables |
| `create_user.py` | Command-line account creation |
| `themes.css` | Source of the theme colours used by the theme picker |
| `requirements.txt` | Python dependencies |
| `start_mac.sh`, `create_user_mac.sh` | macOS launchers |
| `start_windows.bat`, `create_user_windows.bat` | Windows launchers |
| `start_termux.sh`, `create_user_termux.sh` | Android/Termux launchers |

These are created on the first run. They hold your data, so keep them private:

| Path | Contents |
|---|---|
| `data/nestdrive.db` | Accounts and the file list (SQLite) |
| `storage/.objects/` | The actual file contents, named by their SHA-256 hash |
| `.nestdrive_secret` | Key used to sign login sessions |
| `.venv/` | Python environment (safe to delete; it is rebuilt on the next start) |

## Backup and moving to another device

1. Stop the server.
2. Copy `data/`, `storage/` and `.nestdrive_secret` to the same place in the new copy of NestDrive.
3. Start it again.

Note: accounts created on Termux or Windows use scrypt password hashes. Apple's built-in Python cannot check scrypt hashes, so those users cannot sign in on a Mac that uses it. Either install Python from python.org or Homebrew (`brew install python`, then delete `.venv`), or create the accounts again.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Address already in use` | NestDrive is already running, or another app uses port 8081. Stop the other one, or start with `NESTDRIVE_PORT=8090`. |
| Other devices can't connect | Start with `NESTDRIVE_HOST=0.0.0.0`, check that both devices are on the same Wi-Fi, and allow the connection in the firewall. |
| "Invalid filename" on upload | Names are reduced to plain ASCII letters, digits, `.`, `_` and `-`. A name made only of other characters (for example Hindi or emoji) ends up empty. Rename the file first. |
| Termux stops when the screen is off | Run `termux-wake-lock` before starting, or keep Termux in the foreground. |
| Setup fails while installing Flask | Check the internet connection and run the start script again. |

## Security

NestDrive is meant for a **trusted home network**. Do not expose it to the internet as it is.

- Traffic is plain HTTP. On shared Wi-Fi, passwords and files can be read by others on the network.
- Anyone who can reach the server can register an account.
- There is no limit on login attempts.
- It runs on Flask's built-in development server.
- Never share `data/`, `storage/`, `.nestdrive_secret` or backups publicly.

## License

Released under the [MIT License](LICENSE). © 2026 harshyad24
