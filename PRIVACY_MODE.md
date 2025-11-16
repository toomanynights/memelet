# Privacy Mode Feature

## Overview
Memelet now supports two privacy modes: **Private** (default) and **Public**. This allows you to control who can view your meme collection.

## Privacy Modes

### 🔐 Private Mode (Default)
- **Authentication required for all pages**
- Only logged-in users can access any part of the site
- Current behavior - login required everywhere
- Most secure option

### 🌍 Public Mode
- **Read-only access for visitors**
- Visitors (unauthenticated users) can:
  - View the meme collection (index page)
  - Browse individual meme pages
  - See descriptions, tags, and all metadata
  - Use search and filtering
  - View Clippy agent (if enabled)
  - Navigate between memes

- Visitors **cannot**:
  - Upload memes
  - Edit meme details
  - Delete memes
  - Add or remove tags
  - Access the Settings page
  - Access the Tags management page
  - Trigger AI analysis or tag scans
  - Reorder album items

- Visitors can:
  - Click "Sign In" link in the menu to authenticate
  - Access full functionality after logging in

## How to Change Privacy Mode

### Via Settings Page
1. Log in to your Memelet
2. Go to **Settings** (⚙️ Settings in menu)
3. Scroll to **🔒 Privacy Mode** section
4. Select your preferred mode:
   - **🔐 Private** - Login required for everything
   - **🌍 Public** - Read-only public access
5. Changes take effect immediately

### Via API
```bash
# Get current privacy mode
curl http://localhost:5000/api/settings/privacy-mode \
  -H "Cookie: session=YOUR_SESSION_COOKIE"

# Set to public mode
curl http://localhost:5000/api/settings/privacy-mode \
  -X POST \
  -H "Content-Type: application/json" \
  -H "Cookie: session=YOUR_SESSION_COOKIE" \
  -d '{"privacy_mode": "public"}'

# Set to private mode
curl http://localhost:5000/api/settings/privacy-mode \
  -X POST \
  -H "Content-Type: application/json" \
  -H "Cookie: session=YOUR_SESSION_COOKIE" \
  -d '{"privacy_mode": "private"}'
```

## Database Schema

The privacy mode setting is stored in the `settings` table:

```sql
-- Setting key: 'privacy_mode'
-- Setting values: 'private' or 'public'
SELECT * FROM settings WHERE key = 'privacy_mode';
```

Default value: `'private'`

## Implementation Details

### Backend (app.py)

#### New Decorator
```python
@login_required_unless_public
def index():
    # This route allows public access if in public mode
    # Otherwise requires authentication
```

#### Helper Functions
- `is_public_mode()` - Check if site is in public mode
- `login_required_unless_public` - Conditional authentication decorator

#### Protected Routes
Routes that **always require authentication**:
- `/settings` - Settings page
- `/tags` - Tag management
- `/logout` - Logout
- All `/api/*` endpoints (except GET on read-only data)

Routes that **conditionally allow public access**:
- `/` - Index page (meme grid)
- `/meme/<id>` - Individual meme detail page
  - POST requests (editing) always require authentication
  - GET requests allowed in public mode

### Frontend (Templates)

#### Conditional UI Elements
All templates now check `current_user.is_authenticated` to show/hide:
- Upload button
- Edit forms
- Delete buttons
- Bulk actions (select, tag, delete)
- Settings and Tags menu items
- Analysis/scan buttons
- Album reordering controls

#### Visitor Experience
When not authenticated in public mode:
- Menu shows "🔑 Sign In" instead of logout
- Meme details shown as read-only text boxes
- Sign-in prompt displayed: "🔒 Sign in to edit meme details"
- Clean, read-only browsing experience

## Use Cases

### Private Mode Use Cases
- **Personal collection** - Keep your memes private
- **Development/Testing** - Secure during development
- **Sensitive content** - Ensure privacy
- **Default security** - Best for most users

### Public Mode Use Cases
- **Share with friends** - Let others browse your collection
- **Public gallery** - Showcase your memes
- **Portfolio** - Display meme curation skills
- **Collaboration** - Allow viewing while restricting edits
- **Demo** - Show off your Memelet without sharing credentials

## Security Notes

1. **Authentication still required for edits**: Even in public mode, all write operations require authentication
2. **Settings always protected**: Settings page never accessible to visitors
3. **Tag management restricted**: Visitors can view tags but not manage them
4. **Direct file links**: Static files remain accessible (as before) regardless of mode
5. **Session security**: Existing authentication security features unchanged

## Testing

### Test Public Mode
1. Set privacy mode to "Public" in settings
2. Open an incognito/private browser window
3. Navigate to your Memelet URL
4. Verify:
   - ✅ Can view meme grid
   - ✅ Can click on memes
   - ✅ Can see descriptions and tags
   - ✅ Can search and filter
   - ✅ Menu shows "Sign In" option
   - ❌ Cannot see upload button
   - ❌ Cannot see edit forms
   - ❌ Cannot access /settings
   - ❌ Cannot access /tags

### Test Private Mode
1. Set privacy mode to "Private" in settings
2. Open an incognito/private browser window
3. Navigate to your Memelet URL
4. Verify:
   - Immediately redirected to /login
   - Cannot access any page without authentication

## Migration

If upgrading from a previous version:

1. Run database initialization to add the new setting:
   ```bash
   python init_database.py
   ```

2. This will add `privacy_mode` setting with default value `'private'`
3. Behavior is identical to before (authentication required everywhere)
4. Optionally switch to public mode via settings

## Troubleshooting

### Public mode not working
- Check database: `SELECT value FROM settings WHERE key = 'privacy_mode'`
- Should return `'public'`
- Try restarting the Flask app
- Clear browser cookies and try in incognito

### Still seeing edit buttons as visitor
- Hard refresh the page (Ctrl+Shift+R)
- Check browser console for JavaScript errors
- Verify you're actually logged out

### Can't access settings in public mode
- This is correct behavior
- Settings page always requires authentication
- Log in to access settings

## API Endpoints

### Get Privacy Mode
```
GET /api/settings/privacy-mode
Authentication: Required
Response: {"success": true, "privacy_mode": "private"|"public"}
```

### Set Privacy Mode
```
POST /api/settings/privacy-mode
Authentication: Required
Body: {"privacy_mode": "private"|"public"}
Response: {"success": true, "privacy_mode": "private"|"public"}
```

## Future Enhancements

Possible future additions:
- Per-tag visibility controls
- IP-based access restrictions
- Password protection for public mode
- Read-only API tokens for integrations
- Analytics for visitor views
- Custom landing page for public mode

