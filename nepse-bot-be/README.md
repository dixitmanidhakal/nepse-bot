# NEPSE Trading Bot

A **configuration-driven NEPSE trading bot** that generates buy signals based on a comprehensive 4-component trading strategy with precise entry/exit points and risk management.

## 🎯 Features

- **Sector Identification**: Analyze sectors and identify bullish trends
- **Liquidity Hunt**: Detect demand zones and optimal entry points
- **Market Depth Analysis**: Monitor order book and detect institutional activity
- **Floorsheet Analysis**: Track broker activity and detect manipulation
- **Risk Management**: Calculate position sizing, stop-loss, and take-profit
- **Configuration-Driven**: Easy to customize via database configuration
- **RESTful API**: FastAPI-based API with automatic documentation
- **Modular Architecture**: Easy to extend and maintain

## 📋 Prerequisites

Before you begin, ensure you have the following installed:

- **Python 3.10+** - [Download Python](https://www.python.org/downloads/)
- **PostgreSQL 14+** - [Download PostgreSQL](https://www.postgresql.org/download/)
- **TA-Lib** (Optional, for technical analysis)
  - macOS: `brew install ta-lib`
  - Ubuntu: `sudo apt-get install ta-lib`
  - Windows: [Download from here](https://github.com/mrjbq7/ta-lib#windows)

## ⚡ One-command runner

```bash
./run.sh                 # macOS / Linux (auto-creates venv + installs deps)
run.bat                  # Windows
```

After that, browse to:

- API root : http://localhost:8000
- Swagger  : http://localhost:8000/docs
- ReDoc    : http://localhost:8000/redoc

Environment overrides:

| Env var   | Default   | Purpose                            |
| --------- | --------- | ---------------------------------- |
| `HOST`    | `0.0.0.0` | Bind address                       |
| `PORT`    | `8000`    | Bind port                          |
| `RELOAD`  | `0`       | Set to `1` for uvicorn `--reload`  |

### Run the test suite

```bash
./venv/bin/pytest        # 71 tests (unit + api + integration)
```

## 🚀 Quick Start

### 1. Clone the Repository

```bash
cd nepse-bot-be
```

### 2. Set Up Virtual Environment

**Option A: Using the setup script (Recommended)**

```bash
chmod +x setup_venv.sh
./setup_venv.sh
```

**Option B: Manual setup**

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate  # On Windows

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Update the `.env` file with your database credentials:

```bash
# Database Configuration
DATABASE_URL=postgresql://your_username:your_password@localhost:5432/nepse_bot
DB_USER=your_username
DB_PASSWORD=your_password
```

### 4. Set Up PostgreSQL Database

```bash
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE nepse_bot;

# Exit PostgreSQL
\q
```

### 5. Test Database Connection

```bash
python test_connection.py
```

You should see:

```
✅ SUCCESS: Database connection is working!
```

### 6. Run the Application

```bash
python app/main.py
```

Or using uvicorn directly:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 7. Access API Documentation

Open your browser and navigate to:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## 📁 Project Structure

```
nepse-bot-be/
├── app/
│   ├── __init__.py              # Application package
│   ├── main.py                  # FastAPI application entry point
│   ├── config.py                # Configuration management
│   ├── database.py              # Database connection & session management
│   ├── models/                  # SQLAlchemy models (Day 2)
│   │   └── __init__.py
│   ├── api/                     # API routes (Day 3+)
│   │   ├── __init__.py
│   │   └── v1/
│   │       └── __init__.py
│   ├── components/              # Strategy components (Day 8+)
│   │   └── __init__.py
│   ├── indicators/              # Technical indicators (Day 6-7)
│   │   └── __init__.py
│   ├── services/                # External services
│   │   ├── __init__.py
│   │   ├── base_api_client.py   # Abstract API client interface
│   │   └── nepse_api_client.py  # NEPSE API implementation
│   └── utils/                   # Utility functions
│       └── __init__.py
├── tests/                       # Test files
│   └── __init__.py
├── logs/                        # Log files (auto-created)
├── .env                         # Environment variables
├── .env.example                 # Example environment file
├── .gitignore                   # Git ignore file
├── requirements.txt             # Python dependencies
├── setup_venv.sh               # Virtual environment setup script
├── test_connection.py          # Database connection test
└── README.md                   # This file
```

## 🏗️ Architecture

### Modular Design

The application follows a **modular architecture** with clear separation of concerns:

1. **Configuration Layer** (`config.py`)

   - Centralized configuration management
   - Environment-based settings
   - Type-safe configuration access

2. **Database Layer** (`database.py`)

   - Connection pooling
   - Session management
   - Health checks

3. **Service Layer** (`services/`)

   - Abstract interfaces for external APIs
   - Easy to swap implementations
   - NEPSE API client with retry logic

4. **API Layer** (`api/`)

   - RESTful endpoints
   - Request validation
   - Response formatting

5. **Business Logic** (`components/`)
   - Strategy components
   - Signal generation
   - Risk management

### API Client Architecture

The API client uses an **abstract base class** pattern for flexibility:

```python
# Abstract interface
class BaseAPIClient(ABC):
    @abstractmethod
    def fetch_market_indices(self): pass

    @abstractmethod
    def fetch_stock_list(self): pass
    # ... more methods

# NEPSE implementation
class NepseAPIClient(BaseAPIClient):
    def fetch_market_indices(self):
        # NEPSE-specific implementation
        pass

# Easy to add alternative providers
class AlternativeAPIClient(BaseAPIClient):
    def fetch_market_indices(self):
        # Alternative API implementation
        pass

# Factory function for easy switching
client = create_api_client("nepse")  # or "alternative"
```

**Benefits:**

- ✅ Easy to switch API providers
- ✅ Consistent interface across providers
- ✅ Easy to test with mock implementations
- ✅ Future-proof design

## 🔧 Configuration

All configuration is managed through environment variables in `.env`:

### Application Settings

```bash
APP_NAME=NEPSE Trading Bot
APP_VERSION=1.0.0
ENVIRONMENT=development
DEBUG=True
```

### Database Settings

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/nepse_bot
```

### API Settings

```bash
NEPSE_API_BASE_URL=https://www.nepalstock.com.np/api
NEPSE_API_TIMEOUT=30
NEPSE_API_RETRY_ATTEMPTS=3
```

### Trading Settings

```bash
DEFAULT_RISK_PERCENTAGE=1.0
MAX_RISK_PERCENTAGE=2.0
DEFAULT_REWARD_RISK_RATIO=2.0
```

## 🧪 Testing

### Test Database Connection

```bash
python test_connection.py
```

### Test API Connection

```bash
curl http://localhost:8000/test-api
```

### Run Unit Tests (Coming in Day 21)

```bash
pytest
```

## 📊 API Endpoints

### Core Endpoints

- `GET /` - Root endpoint with API information
- `GET /health` - Health check (database + API)
- `GET /docs` - Swagger UI documentation
- `GET /redoc` - ReDoc documentation

### Database Endpoints

- `GET /db-info` - Database connection information
- `GET /test-db` - Test database connection

### API Endpoints

- `GET /test-api` - Test NEPSE API connection
- `GET /config` - Get application configuration

### Future Endpoints (Coming Soon)

- `GET /api/v1/signals` - List active signals
- `GET /api/v1/signals/{id}` - Get signal details
- `POST /api/v1/signals/generate` - Generate new signals
- `GET /api/v1/bot-configs` - List bot configurations
- And more...

## 🔍 Troubleshooting

### Database Connection Issues

**Problem**: `Database connection failed`

**Solutions**:

1. Check if PostgreSQL is running: `pg_isready`
2. Verify database exists: `psql -U postgres -l | grep nepse_bot`
3. Check credentials in `.env` file
4. Ensure PostgreSQL accepts connections: Check `pg_hba.conf`

### TA-Lib Installation Issues

**Problem**: `Failed to install TA-Lib`

**Solutions**:

- **macOS**: `brew install ta-lib`
- **Ubuntu**: `sudo apt-get install ta-lib`
- **Windows**: Download from [GitHub](https://github.com/mrjbq7/ta-lib#windows)

### Port Already in Use

**Problem**: `Address already in use`

**Solution**:

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or use a different port
uvicorn app.main:app --port 8001
```

## 📝 Development Roadmap

- [x] **Day 1**: Environment Setup ✅
- [ ] **Day 2**: Database Foundation
- [ ] **Day 3**: Base Architecture
- [ ] **Day 4**: NEPSE API Client
- [ ] **Day 5**: Data Storage
- [ ] **Day 6-7**: Technical Indicators
- [ ] **Day 8-14**: Strategy Components
- [ ] **Day 15-16**: Signal Generation & Risk Management
- [ ] **Day 17-19**: API Endpoints & Scheduling
- [ ] **Day 20-21**: Testing & Deployment

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Write tests
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License.

## 📧 Support

For issues and questions:

- Create an issue on GitHub
- Check the documentation at `/docs`
- Review the TODO.md file for implementation details

## 🙏 Acknowledgments

- Nepal Stock Exchange (NEPSE) for market data
- FastAPI for the excellent web framework
- SQLAlchemy for database ORM
- TA-Lib for technical analysis

---

**Built with ❤️ for NEPSE traders**
