# NEPSE Trading Bot - Day 1 Setup Guide

## 📋 Complete Setup Instructions

This guide will walk you through setting up the NEPSE Trading Bot from scratch.

---

## ✅ Day 1 Checklist

- [x] Project structure created
- [x] Configuration files created
- [x] Database connection module implemented
- [x] API client architecture implemented
- [x] FastAPI application created
- [ ] Virtual environment setup (You need to do this)
- [ ] PostgreSQL database created (You need to do this)
- [ ] Dependencies installed (You need to do this)
- [ ] Database connection tested (You need to do this)

---

## 🚀 Step-by-Step Setup

### Step 1: Verify Prerequisites

Check if you have the required software installed:

```bash
# Check Python version (should be 3.10+)
python3 --version

# Check PostgreSQL (should be installed)
psql --version

# Check if PostgreSQL is running
pg_isready
```

**If not installed:**

- Python: https://www.python.org/downloads/
- PostgreSQL: https://www.postgresql.org/download/

---

### Step 2: Set Up Virtual Environment

**Option A: Automated Setup (Recommended)**

```bash
./setup_venv.sh
```

This script will:

- Create virtual environment
- Install all dependencies
- Create logs directory
- Copy .env.example to .env

**Option B: Manual Setup**

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # macOS/Linux
# OR
venv\Scripts\activate  # Windows

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Create logs directory
mkdir -p logs
```

---

### Step 3: Install TA-Lib (Optional but Recommended)

TA-Lib is used for technical analysis calculations.

**macOS:**

```bash
brew install ta-lib
```

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install ta-lib
```

**Windows:**
Download from: https://github.com/mrjbq7/ta-lib#windows

**Note:** You can skip this for now and install it later when needed.

---

### Step 4: Set Up PostgreSQL Database

**Option A: Using psql command line**

```bash
# Connect to PostgreSQL as postgres user
psql -U postgres

# Create database
CREATE DATABASE nepse_bot;

# Create user (optional, if you want a dedicated user)
CREATE USER nepse_user WITH PASSWORD 'your_password';

# Grant privileges
GRANT ALL PRIVILEGES ON DATABASE nepse_bot TO nepse_user;

# Exit
\q
```

**Option B: Using pgAdmin**

1. Open pgAdmin
2. Right-click on "Databases"
3. Select "Create" → "Database"
4. Name: `nepse_bot`
5. Click "Save"

---

### Step 5: Configure Environment Variables

Edit the `.env` file with your database credentials:

```bash
# Open .env file in your editor
nano .env  # or use any text editor
```

Update these lines:

```bash
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/nepse_bot
DB_USER=postgres
DB_PASSWORD=your_password
```

**Important:** Replace `your_password` with your actual PostgreSQL password!

---

### Step 6: Test Database Connection

```bash
# Make sure virtual environment is activated
source venv/bin/activate  # if not already activated

# Run connection test
python test_connection.py
```

**Expected Output:**

```
========================================
NEPSE Trading Bot - Database Connection Test
========================================

Configuration:
  Database: nepse_bot
  Host: localhost
  Port: 5432
  User: postgres
  Environment: development

----------------------------------------------------------

Testing database connection...

✅ SUCCESS: Database connection is working!

Database Information:
  Host: localhost
  Port: 5432
  Database: nepse_bot
  User: postgres
  Pool Size: 5
  Active Connections: 0
  Overflow: 0

========================================
You can now proceed with the application!
========================================
```

---

### Step 7: Run the Application

```bash
# Make sure virtual environment is activated
source venv/bin/activate

# Run the application
python app/main.py
```

**Expected Output:**

```
============================================================
Starting NEPSE Trading Bot v1.0.0
Environment: development
============================================================
Testing database connection...
✅ Database connection successful
Connected to: nepse_bot at localhost:5432
✅ Database tables initialized
Testing NEPSE API connection...
⚠️  NEPSE API health check failed
============================================================
Application started successfully!
API Documentation: http://0.0.0.0:8000/docs
============================================================
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**Note:** NEPSE API health check may fail because the endpoints are placeholders. This is expected for Day 1.

---

### Step 8: Test the API

Open your browser and visit:

1. **API Documentation (Swagger UI)**

   - URL: http://localhost:8000/docs
   - Interactive API documentation

2. **Alternative Documentation (ReDoc)**

   - URL: http://localhost:8000/redoc
   - Clean, readable documentation

3. **Health Check**

   - URL: http://localhost:8000/health
   - Check application health status

4. **Test Database**

   - URL: http://localhost:8000/test-db
   - Verify database connection

5. **Root Endpoint**
   - URL: http://localhost:8000/
   - Basic API information

---

## 🎯 Architecture Overview

### Project Structure Explained

```
nepse-bot-be/
├── app/                          # Main application package
│   ├── __init__.py              # Package initialization
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Configuration management (Pydantic Settings)
│   ├── database.py              # Database connection & session management
│   │
│   ├── services/                # External service integrations
│   │   ├── base_api_client.py   # Abstract API client (Interface)
│   │   └── nepse_api_client.py  # NEPSE API implementation
│   │
│   ├── models/                  # SQLAlchemy models (Day 2)
│   ├── api/v1/                  # API routes (Day 3+)
│   ├── components/              # Strategy components (Day 8+)
│   ├── indicators/              # Technical indicators (Day 6-7)
│   └── utils/                   # Utility functions
│
├── tests/                       # Test files
├── logs/                        # Application logs
├── .env                         # Environment variables (DO NOT COMMIT)
├── .env.example                 # Example environment file
├── requirements.txt             # Python dependencies
├── setup_venv.sh               # Setup automation script
├── test_connection.py          # Database connection test
└── README.md                   # Project documentation
```

### Key Design Principles

#### 1. **Configuration-Driven Architecture**

All settings are managed through `config.py` using Pydantic Settings:

```python
from app.config import settings

# Access any setting
database_url = settings.database_url
api_url = settings.nepse_api_base_url
```

**Benefits:**

- ✅ Type-safe configuration
- ✅ Environment variable validation
- ✅ Easy to change settings without code changes
- ✅ Centralized configuration management

#### 2. **Abstract API Client Pattern**

The API client uses an abstract base class for flexibility:

```python
# Define interface
class BaseAPIClient(ABC):
    @abstractmethod
    def fetch_market_indices(self): pass

# Implement for NEPSE
class NepseAPIClient(BaseAPIClient):
    def fetch_market_indices(self):
        # NEPSE-specific implementation
        pass

# Easy to add alternatives
class AlternativeAPIClient(BaseAPIClient):
    def fetch_market_indices(self):
        # Alternative implementation
        pass

# Factory function for easy switching
client = create_api_client("nepse")  # or "alternative"
```

**Benefits:**

- ✅ Easy to switch API providers
- ✅ Consistent interface
- ✅ Easy to test with mocks
- ✅ Future-proof design

#### 3. **Database Session Management**

SQLAlchemy with dependency injection:

```python
from app.database import get_db

@app.get("/items")
def get_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
```

**Benefits:**

- ✅ Automatic session lifecycle
- ✅ Connection pooling
- ✅ Easy to test
- ✅ No manual session management

#### 4. **Modular Component Design**

Each strategy component will be independent:

```python
class SectorIdentifier(BaseComponent):
    def analyze(self, config):
        # Sector analysis logic
        pass

class LiquidityHunter(BaseComponent):
    def analyze(self, config):
        # Liquidity analysis logic
        pass
```

**Benefits:**

- ✅ Easy to enable/disable components
- ✅ Independent testing
- ✅ Reusable across different strategies
- ✅ Clear separation of concerns

---

## 🔧 Configuration Details

### Environment Variables Explained

#### Application Settings

```bash
APP_NAME=NEPSE Trading Bot          # Application name
APP_VERSION=1.0.0                   # Version number
ENVIRONMENT=development             # Environment (development/production)
DEBUG=True                          # Enable debug mode
```

#### Server Configuration

```bash
HOST=0.0.0.0                        # Server host (0.0.0.0 = all interfaces)
PORT=8000                           # Server port
```

#### Database Configuration

```bash
# Full connection string (recommended)
DATABASE_URL=postgresql://user:password@host:port/database

# Or individual settings
DB_HOST=localhost                   # Database host
DB_PORT=5432                        # Database port
DB_NAME=nepse_bot                   # Database name
DB_USER=postgres                    # Database user
DB_PASSWORD=your_password           # Database password
```

#### API Configuration

```bash
NEPSE_API_BASE_URL=https://www.nepalstock.com.np/api
NEPSE_API_TIMEOUT=30                # Request timeout (seconds)
NEPSE_API_RETRY_ATTEMPTS=3          # Number of retries on failure
```

#### Trading Configuration

```bash
DEFAULT_RISK_PERCENTAGE=1.0         # Default risk per trade (1%)
MAX_RISK_PERCENTAGE=2.0             # Maximum risk per trade (2%)
DEFAULT_REWARD_RISK_RATIO=2.0       # Reward:Risk ratio (2:1)
```

---

## 🐛 Troubleshooting

### Issue 1: Virtual Environment Not Activating

**Problem:**

```bash
bash: venv/bin/activate: No such file or directory
```

**Solution:**

```bash
# Make sure you created the virtual environment first
python3 -m venv venv

# Then activate it
source venv/bin/activate
```

---

### Issue 2: PostgreSQL Connection Failed

**Problem:**

```
❌ Database connection failed: could not connect to server
```

**Solutions:**

1. **Check if PostgreSQL is running:**

```bash
pg_isready
# Should output: /tmp:5432 - accepting connections
```

2. **Start PostgreSQL if not running:**

```bash
# macOS (Homebrew)
brew services start postgresql

# Ubuntu/Debian
sudo systemctl start postgresql

# Windows
# Start PostgreSQL service from Services app
```

3. **Verify database exists:**

```bash
psql -U postgres -l | grep nepse_bot
```

4. **Check credentials in .env file**

---

### Issue 3: TA-Lib Installation Failed

**Problem:**

```
ERROR: Could not build wheels for TA-Lib
```

**Solution:**

Install system-level TA-Lib first:

**macOS:**

```bash
brew install ta-lib
pip install TA-Lib
```

**Ubuntu:**

```bash
sudo apt-get install ta-lib
pip install TA-Lib
```

**Windows:**
Download pre-built wheel from: https://github.com/mrjbq7/ta-lib#windows

---

### Issue 4: Port 8000 Already in Use

**Problem:**

```
ERROR: [Errno 48] Address already in use
```

**Solution:**

**Option A: Kill the process using port 8000**

```bash
# Find process
lsof -i :8000

# Kill it
kill -9 <PID>
```

**Option B: Use a different port**

```bash
# Edit .env file
PORT=8001

# Or run with custom port
uvicorn app.main:app --port 8001
```

---

### Issue 5: Import Errors

**Problem:**

```
ModuleNotFoundError: No module named 'app'
```

**Solution:**

Make sure you're running from the project root directory:

```bash
# Check current directory
pwd
# Should be: /path/to/nepse-bot-be

# If not, navigate to project root
cd /path/to/nepse-bot-be

# Then run
python app/main.py
```

---

## 📚 Next Steps

After completing Day 1 setup, you're ready for:

### Day 2: Database Foundation

- Create SQLAlchemy models
- Set up Alembic migrations
- Create bot_configurations table
- Insert default configurations

### Day 3: Base Architecture

- Create BaseComponent class
- Set up API routes structure
- Create bot configuration endpoints

### Day 4: NEPSE API Client

- Implement actual NEPSE API endpoints
- Add error handling and retry logic
- Test with real NEPSE data

---

## 🎉 Success Criteria for Day 1

You've successfully completed Day 1 if:

- ✅ Virtual environment is created and activated
- ✅ All dependencies are installed
- ✅ PostgreSQL database is created
- ✅ Database connection test passes
- ✅ FastAPI application starts without errors
- ✅ You can access http://localhost:8000/docs
- ✅ Health check endpoint returns success

---

## 📞 Getting Help

If you encounter issues:

1. Check this troubleshooting guide
2. Review the README.md file
3. Check application logs in `logs/` directory
4. Review FastAPI documentation: https://fastapi.tiangolo.com/
5. Review SQLAlchemy documentation: https://docs.sqlalchemy.org/

---

**Congratulations! You've completed Day 1! 🎉**

The foundation is now set for building the complete NEPSE Trading Bot.
