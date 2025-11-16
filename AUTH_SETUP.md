# Authorization System Implementation

## Overview
A complete authorization system has been implemented for the Memelet project with the following features:

- **Login required for all pages** (except login page itself and static files)
- **No signup functionality** - pre-configured admin account
- **Direct file links work** - static files remain accessible
- **2-week session cookie lifetime** - users stay logged in for 14 days
- **No Clippy on login page** - clean, professional login interface
- **Password stored securely** - hashed with werkzeug.security
- **Password change functionality** - users can update their password via settings

## Setup Instructions

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

The new dependency added:
- `Flask-Login>=0.6.0` - for session management

### 2. Initialize/Update Database
Run the database initialization script to create the users table:

```bash
python init_database.py
```

This will:
- Create a `users` table with columns: id, username, password_hash, created_at, updated_at
- Create a default admin user with credentials:
  - **Username:** `admin`
  - **Password:** `admin`

**⚠️ IMPORTANT:** Change the default password immediately after first login!

### 3. Configure Secret Key (Optional but Recommended)
Set an environment variable for the Flask secret key for better security:

```bash
export SECRET_KEY='your-secret-key-here'
```

If not set, the app will generate a random key on each restart (which will log out all users).

### 4. Start the Application
```bash
python app.py
```

The app will start on `http://127.0.0.1:5000`

## What Changed

### Database Changes (`init_database.py`)
- Added `users` table with fields:
  - `id` (PRIMARY KEY)
  - `username` (UNIQUE, NOT NULL)
  - `password_hash` (NOT NULL)
  - `created_at` (TIMESTAMP)
  - `updated_at` (TIMESTAMP)
- Automatically creates default admin user on first run

### Application Changes (`app.py`)
1. **New imports:**
   - `Flask-Login` components
   - `check_password_hash`, `generate_password_hash` from werkzeug
   - `redirect`, `url_for`, `flash` from Flask
   - `timedelta` for session lifetime

2. **Flask-Login setup:**
   - Configured LoginManager
   - Set session lifetime to 14 days
   - Created User class for Flask-Login
   - Added user_loader function

3. **New routes:**
   - `GET/POST /login` - Login page and authentication
   - `GET /logout` - Logout functionality
   - `POST /api/settings/change-password` - Password change API

4. **Protected routes:**
   - All existing routes now have `@login_required` decorator
   - Static files (CSS, JS, images) remain accessible
   - Direct file links work (served through static folder)

### Template Changes

#### New Template: `templates/login.html`
- Clean, modern login interface
- Gradient background design
- Form validation
- Error message display
- No Clippy agent
- Responsive design

#### Updated: `templates/settings.html`
- Added password change section
- Three fields: Current Password, New Password, Confirm Password
- JavaScript function for password change with validation
- Success/error message display
- Enter key support for form submission

#### Updated Navigation (All Templates)
- Added "Logout" link to navigation menus
- `index.html` - Added to dropdown menu
- `settings.html` - Added next to back button
- `tags.html` - Added next to Create Tag button
- `meme_detail.html` - Added next to back button

## Security Features

1. **Password Hashing:** All passwords are hashed using werkzeug's `generate_password_hash()` with default settings (pbkdf2:sha256)

2. **Session Management:** Flask-Login handles secure session management with:
   - Session protection set to 'strong'
   - 14-day persistent sessions (remember me)
   - Automatic session refresh

3. **CSRF Protection:** Built into Flask forms (already present)

4. **Login Flow:**
   - Failed login attempts show generic error message
   - No indication of whether username or password was incorrect
   - Usernames are case-sensitive

5. **Password Requirements:**
   - Minimum 4 characters (configurable in code)
   - Must provide current password to change
   - New password must match confirmation

## Usage

### First Time Setup
1. Start the application
2. Navigate to `http://127.0.0.1:5000`
3. You'll be redirected to `/login`
4. Login with:
   - Username: `admin`
   - Password: `admin`
5. Go to Settings → Change Password
6. Update to a secure password

### Changing Password
1. Go to Settings page (⚙️ Settings in menu)
2. Scroll to "🔐 Change Password" section
3. Enter current password
4. Enter new password twice
5. Click "🔒 Change Password"

### Logout
Click the "🚪 Logout" link in any navigation menu

## File Access Behavior

- **Protected:** All HTML pages require login
- **Public:** Static files in `/static/` directory (CSS, JS, images)
- **Direct Links:** Files served through Flask's static file serving remain accessible
  - This allows sharing direct links to meme files
  - Example: `https://memes.tmn.name/files/image.jpg` (based on MEMES_URL_BASE)

## Troubleshooting

### "Please log in to access this page"
- Session expired or invalid
- Solution: Login again at `/login`

### Can't login with admin/admin
- Database not initialized
- Solution: Run `python init_database.py`

### All sessions lost after restart
- No SECRET_KEY environment variable set
- Solution: Set `SECRET_KEY` environment variable

### Password change fails
- Current password incorrect
- New passwords don't match
- Password too short (< 4 characters)
- Solution: Check error message and correct the issue

## Default Credentials

**⚠️ SECURITY WARNING:**
```
Username: admin
Password: admin
```

**Change these immediately after first login!**

## Adding More Users

To add more users, you'll need to manually insert into the database:

```python
from werkzeug.security import generate_password_hash
import sqlite3

conn = sqlite3.connect('memelet.db')
cursor = conn.cursor()
cursor.execute(
    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
    ('newuser', generate_password_hash('newpassword'))
)
conn.commit()
conn.close()
```

Or create a user management page in the future.

## Architecture Notes

- **No signup page:** As requested, only pre-configured accounts
- **Session-based auth:** Uses Flask-Login's session management
- **Cookie lifetime:** 14 days as specified
- **Protected routes:** All application routes require authentication
- **Unprotected routes:** Login page, logout, favicon, static files

