# Fix for Render Port Binding Issue

## Problem
The app is binding to `127.0.0.1:8000` (localhost) instead of `0.0.0.0:$PORT`, causing Render to not detect the open port.

## Root Cause
Render dashboard has a custom start command configured that's overriding the Procfile:
- Current: `uvicorn app.main:app --reload` (wrong - uses localhost and hardcoded port)
- Should be: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Solution Applied

1. **Created `start.sh`** - Startup script that ensures correct host/port
2. **Updated `Procfile`** - Now uses the startup script

## Additional Fix Required in Render Dashboard

You need to update the start command in Render dashboard:

1. Go to **Render Dashboard** → Your Service
2. Click **Settings** → **Build & Deploy**
3. Under **Start Command**, change it to:
   ```
   bash start.sh
   ```
   OR
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
4. **Remove `--reload`** flag (it's for development only)
5. Click **Save Changes**
6. Render will automatically redeploy

## Alternative: Clear Start Command

If you want to use the Procfile:
1. Go to **Settings** → **Build & Deploy**
2. **Clear/Delete** the Start Command field (leave it empty)
3. Render will use the Procfile instead
4. Save and redeploy

## Verify Fix

After updating, check logs for:
- ✅ `INFO:     Uvicorn running on http://0.0.0.0:XXXX` (not 127.0.0.1)
- ✅ Port should match Render's $PORT environment variable
- ✅ No "No open ports detected" errors
- ✅ Service status should be "Live"

---

*The startup script ensures correct binding regardless of how Render calls it*
