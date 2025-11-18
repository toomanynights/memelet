# Memelet

Your personal meme collection manager with AI-powered descriptions.

## Features
- 🤖 AI-powered meme analysis via Replicate API
- 📁 Automatic meme scanning and organization
- 🏷️ Smart tagging system with path-based and AI suggestions
- 🎨 Web interface for browsing, editing, and managing your collection
- 🔍 Advanced search and filtering
- 📊 Album support for multi-image memes
- 🎬 Video and GIF support with thumbnail generation
- 🔐 Authentication and privacy controls

## Quick Start Installation

### Automated Installation (Recommended)

```bash
# Clone or download Memelet
git clone <repository-url> memelet
cd memelet

# Run the interactive installer
python3 install.py
```

The installer will:
- Guide you through configuration
- Create necessary directories
- Generate configuration file
- Set up Python virtual environment (optional)
- Initialize the database
- Set appropriate permissions

### Manual Installation

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create configuration:**
   ```bash
   cp .env.example .env
   nano .env  # Edit with your settings
   ```
   
   Generate a secure SECRET_KEY:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   # Copy output to SECRET_KEY in .env
   ```

3. **Initialize database:**
   ```bash
   python3 init_database.py
   ```

4. **Start Memelet:**
   ```bash
   python3 app.py
   ```

## Configuration

Memelet can be configured via environment variables or a `.env` file. See `.env.example` for all available options.

### Key Configuration Options

- `MEMES_DIR`: Directory where meme files are stored
- `BASE_URL`: Public URL where Memelet is accessible
- `HOST` / `PORT`: Server binding settings
- `TZ`: Timezone for logs and timestamps
- `SECRET_KEY`: Flask secret key for session security (see below)
- `REPLICATE_API_TOKEN`: API token for AI analysis (can also be set in web UI)

### Generating a Secure SECRET_KEY

**Important:** You must set a secure SECRET_KEY for production use!

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and add it to your `.env` file:
```bash
SECRET_KEY=your_generated_key_here
```

The SECRET_KEY is used to:
- Sign session cookies (prevents session hijacking)
- Protect against CSRF attacks
- Secure flash messages

⚠️ **Never commit your SECRET_KEY to version control!**

## First Login

After installation, access the web interface at your configured URL (default: http://localhost:5000)

Default credentials:
- **Username:** admin
- **Password:** admin

⚠️ **Important:** Change the admin password immediately in Settings!

## Usage

### Scanning for Memes

**Via Web UI:**
- Click "Scan & Process" in Settings

**Via Command Line:**
```bash
python3 process_memes.py --scan --process
```

**Automated Scanning (Cron):**
```bash
# Add to crontab for hourly scans
0 * * * * /path/to/memelet/run_scan.sh
```

### Processing Options

```bash
# Scan for new files
python3 process_memes.py --scan

# Process new memes with AI
python3 process_memes.py --process

# Retry failed memes
python3 process_memes.py --retry-errors

# Process specific meme
python3 process_memes.py --process-one <meme_id>

# Scan tags for all memes
python3 process_memes.py --scan-tags-all
```

## Deployment

### Using systemd (Linux)

Create `/etc/systemd/system/memelet.service`:

```ini
[Unit]
Description=Memelet Meme Manager
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/memelet
Environment="PATH=/path/to/memelet/venv/bin"
ExecStart=/path/to/memelet/venv/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable memelet
sudo systemctl start memelet
```

### Behind a Reverse Proxy

Example nginx configuration:
```nginx
server {
    listen 80;
    server_name memes.example.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## API Keys

### Replicate API
Memelet uses Replicate's OpenAI GPT-4.1-mini model for AI analysis.

1. Get your API token from [replicate.com](https://replicate.com)
2. Set it via:
   - `.env` file: `REPLICATE_API_TOKEN=your_token`
   - Web UI: Settings → Replicate API Key

## Directory Structure

```
memelet/
├── app.py              # Main Flask application
├── config.py           # Configuration loader
├── process_memes.py    # Meme processing engine
├── init_database.py    # Database initialization
├── install.py          # Interactive installer
├── requirements.txt    # Python dependencies
├── .env                # Configuration (create from .env.example)
├── files/              # Meme storage
│   ├── _albums/       # Album memes
│   └── _system/       # Thumbnails and temp files
├── logs/              # Application logs
├── static/            # Web assets
├── templates/         # HTML templates
└── memelet.db         # SQLite database
```

## Troubleshooting

### Memes not processing
- Check that Replicate API token is set correctly
- Check logs in `logs/scan.log`
- Verify file permissions

### Can't access web interface
- Check `HOST` and `PORT` in `.env`
- Verify firewall settings
- Check logs for errors

### Import errors
- Ensure virtual environment is activated
- Reinstall dependencies: `pip install -r requirements.txt`

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

See LICENSE file for details.