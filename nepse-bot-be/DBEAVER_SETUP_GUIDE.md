# 🦫 DBeaver Setup Guide for NEPSE Bot Database

## 📋 Your Database Connection Details

Based on your running application, here are your connection details:

- **Host**: `localhost`
- **Port**: `5432`
- **Database**: `nepse_bot`
- **Username**: `dixitmanidhakal`
- **Password**: (none - using system authentication)

---

## 🚀 Step-by-Step Setup in DBeaver

### Step 1: Open DBeaver

- Launch DBeaver application from your Applications folder or Spotlight

### Step 2: Create New Connection

1. Click on **"Database"** in the top menu
2. Select **"New Database Connection"**
   - OR click the **plug icon** (🔌) in the toolbar
   - OR press **⌘+Shift+N** (Mac) or **Ctrl+Shift+N** (Windows/Linux)

### Step 3: Select PostgreSQL

1. In the "Connect to a database" window, find and click **"PostgreSQL"**
2. Click **"Next"** button at the bottom

### Step 4: Enter Connection Settings

Fill in the following details in the "Connection Settings" tab:

#### Main Tab:

```
Host:           localhost
Port:           5432
Database:       nepse_bot
Username:       dixitmanidhakal
Password:       (leave empty)
```

**Important Settings:**

- ✅ Check **"Show all databases"** (optional, to see other databases)
- ✅ Uncheck **"Save password"** (since there's no password)

#### Authentication:

- Select **"Database Native"** from the dropdown

### Step 5: Test Connection

1. Click the **"Test Connection"** button at the bottom left
2. If this is your first time:
   - DBeaver will ask to download PostgreSQL driver
   - Click **"Download"** and wait for it to complete
3. You should see: **"Connected"** with a green checkmark ✅

### Step 6: Finish Setup

1. Click **"Finish"** button
2. Your connection will appear in the "Database Navigator" panel on the left

---

## 🎯 Navigating Your Database in DBeaver

### View Tables

1. In the left panel, expand:

   ```
   nepse_bot
   └── Databases
       └── nepse_bot
           └── Schemas
               └── public
                   └── Tables
   ```

2. You should see your tables:
   - `bot_configurations`
   - `floorsheets`
   - `market_depth`
   - `patterns`
   - `sectors`
   - `signals`
   - `stock_ohlcv`
   - `stocks`

### View Table Data

1. **Right-click** on any table
2. Select **"View Data"** → **"View Data"**
   - OR double-click the table
3. Data will appear in the main panel

### View Table Structure

1. **Right-click** on any table
2. Select **"View Table"**
3. You'll see:
   - **Columns**: Field names, types, constraints
   - **Constraints**: Primary keys, foreign keys
   - **Indexes**: Database indexes
   - **DDL**: SQL code to create the table

### Run SQL Queries

1. Click **"SQL Editor"** button (or press **F3**)
2. Type your query:
   ```sql
   SELECT * FROM stocks LIMIT 10;
   ```
3. Press **⌘+Enter** (Mac) or **Ctrl+Enter** (Windows/Linux) to execute
4. Results appear below

---

## 🔧 Common Tasks in DBeaver

### 1. View All Tables with Row Counts

```sql
SELECT
    schemaname,
    tablename,
    n_live_tup as row_count
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
```

### 2. Export Data to CSV

1. Right-click on a table
2. Select **"Export Data"**
3. Choose **"CSV"** format
4. Select destination and click **"Proceed"**

### 3. Import Data from CSV

1. Right-click on a table
2. Select **"Import Data"**
3. Choose your CSV file
4. Map columns and click **"Proceed"**

### 4. View ER Diagram

1. Right-click on **"public"** schema
2. Select **"View Diagram"**
3. You'll see visual relationships between tables

### 5. Refresh Data

- Press **F5** or click the refresh button to reload data

---

## 🎨 Useful DBeaver Features

### Dark Theme

1. Go to **Window** → **Preferences**
2. Select **User Interface** → **Appearance**
3. Choose **"Dark"** theme

### Auto-Complete

- Start typing SQL and press **Ctrl+Space** for suggestions

### Query History

- Press **Ctrl+H** to see all your previous queries

### Multiple Tabs

- Open multiple SQL editors with **Ctrl+]**

### Bookmarks

- Right-click any query result and select **"Add to Bookmarks"**

---

## 🐛 Troubleshooting

### Issue: "Connection refused"

**Solution:**

1. Make sure PostgreSQL is running:
   ```bash
   pg_isready
   ```
2. If not running, start it:
   ```bash
   brew services start postgresql@14
   ```

### Issue: "Authentication failed"

**Solution:**

1. Try leaving password empty
2. Or check your PostgreSQL authentication settings:
   ```bash
   cat /opt/homebrew/var/postgresql@14/pg_hba.conf
   ```

### Issue: "Driver not found"

**Solution:**

1. Go to **Database** → **Driver Manager**
2. Find **PostgreSQL**
3. Click **"Download/Update"**

### Issue: "Database does not exist"

**Solution:**

1. Verify database exists:
   ```bash
   psql -l | grep nepse_bot
   ```
2. If not, create it:
   ```bash
   createdb nepse_bot
   ```

---

## 📊 Quick Reference: Your Database Schema

Your `nepse_bot` database has these tables:

1. **stocks** - Stock information (symbol, name, sector)
2. **sectors** - Sector information and performance
3. **stock_ohlcv** - OHLCV price data
4. **market_depth** - Order book data
5. **floorsheets** - Trade execution data
6. **patterns** - Detected chart patterns
7. **signals** - Trading signals
8. **bot_configurations** - Bot settings

---

## 🎉 You're All Set!

Your DBeaver is now connected to the `nepse_bot` database. You can:

✅ Browse all tables
✅ View and edit data
✅ Run SQL queries
✅ Export/import data
✅ View table relationships
✅ Monitor database performance

---

## 💡 Pro Tips

1. **Pin Frequently Used Tables**: Right-click → "Add to Favorites"
2. **Use SQL Templates**: Right-click in SQL editor → "SQL Templates"
3. **Enable Auto-Commit**: Toolbar → Toggle "Auto-commit" for instant changes
4. **Use Data Filters**: Click filter icon in data view for quick filtering
5. **Keyboard Shortcuts**: Press **Ctrl+Shift+L** to see all shortcuts

---

**Happy Database Browsing! 🚀**
