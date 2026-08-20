# nepse-bot

NEPSE Trading Bot — Frontend
React + Vite + TypeScript dashboard that consumes the nepse-bot-be FastAPI backend and presents a professional trading research UI.

Features
Introduction / Landing page — explains what the platform does and showcases every capability.
Dashboard — system health, live signals, and DB statistics.
Stock Recommendations — deterministic, explainable buy/watch/avoid ranking across the full NEPSE universe with per-factor breakdowns.
Stock Analysis — per-symbol indicators, patterns, trading signals.
Sector Analysis — sector strength, rotation, bullish leaders.
Stock Screener — filter by momentum, value, beta, growth, etc.
Market Depth — order-book pressure, walls, liquidity scoring.
Floorsheet — broker tracking & institutional-accumulation analytics.
Calendar — trading days, holidays, festival windows.
Quant Lab — regime detection, position sizing, Kelly fraction.
Data Manager — trigger NEPSE data ingestion jobs.
Tech stack
Layer	Choice
Framework	React 18
Build tool	Vite 5
Language	TypeScript (strict)
Styling	TailwindCSS
Data fetching	TanStack Query (v5) + axios
Routing	React Router v6
Icons	lucide-react
Getting started
Fast path — one-command runner
./run.sh                 # macOS / Linux (auto-installs deps)
run.bat                  # Windows
The run.sh / run.bat scripts auto-detect pnpm → npm → yarn, install dependencies on first run, and start the Vite dev server at http://localhost:5173.

Env var	Default	Purpose
PORT	5173	Dev server port
MODE	dev	dev | build | preview
Manual
# Install dependencies
pnpm install       # or npm install / yarn

# Start dev server (talks to http://localhost:8000 by default)
pnpm dev

# Production build
pnpm build
The dev server runs on http://localhost:5173 and proxies API calls to the backend at http://localhost:8000 (configured in src/api/client.ts).

Project structure
src/
├── App.tsx                 # Route map
├── main.tsx                # Entry point
├── api/                    # Typed API clients (one per module)
│   ├── client.ts           # axios instance + error interceptor
│   ├── recommendations.ts  # Recommendation engine client
│   ├── stocks.ts           # Stock screener & beta
│   └── …                   # sectors / indicators / patterns / …
├── components/
│   ├── layout/             # Layout + Sidebar
│   ├── shared/             # StatCard, LoadingSpinner, ErrorMessage
│   └── ui/                 # Re-usable primitives (card, etc.)
├── pages/
│   ├── Intro.tsx           # Landing page
│   ├── Dashboard.tsx       # System dashboard
│   ├── Recommendations.tsx # Top ranked picks
│   └── …                   # analysis / screener / floorsheet / …
└── types/                  # Cross-module TypeScript contracts
Backend contract
Runs against nepse-bot-be which exposes 94+ REST endpoints under /api/v1/*. Interactive docs are available at http://localhost:8000/docs when the backend is running.

License
MIT © Dixit Mani Dhakal


NEPSE Trading Bot
A configuration-driven NEPSE trading bot that generates buy signals based on a comprehensive 4-component trading strategy with precise entry/exit points and risk management.

🎯 Features
Sector Identification: Analyze sectors and identify bullish trends
Liquidity Hunt: Detect demand zones and optimal entry points
Market Depth Analysis: Monitor order book and detect institutional activity
Floorsheet Analysis: Track broker activity and detect manipulation
Risk Management: Calculate position sizing, stop-loss, and take-profit
Configuration-Driven: Easy to customize via database configuration
RESTful API: FastAPI-based API with automatic documentation
Modular Architecture: Easy to extend and maintain
📋 Prerequisites
Before you begin, ensure you have the following installed:

Python 3.10+ - Download Python
PostgreSQL 14+ - Download PostgreSQL
TA-Lib (Optional, for technical analysis)
macOS: brew install ta-lib
Ubuntu: sudo apt-get install ta-lib
Windows: Download from here
⚡ One-command runner
./run.sh                 # macOS / Linux (auto-creates venv + installs deps)
run.bat                  # Windows
After that, browse to:

API root : http://localhost:8000
Swagger : http://localhost:8000/docs
ReDoc : http://localhost:8000/redoc
Environment overrides:

Env var	Default	Purpose
HOST	0.0.0.0	Bind address
PORT	8000	Bind port
RELOAD	0	Set to 1 for uvicorn --reload
Run the test suite
./venv/bin/pytest        # 71 tests (unit + api + integration)
🚀 Quick Start
1. Clone the Repository
cd nepse-bot-be
2. Set Up Virtual Environment
Option A: Using the setup script (Recommended)

chmod +x setup_venv.sh
./setup_venv.sh
Option B: Manual setup

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
3. Configure Environment Variables
Update the .env file with your database credentials:

# Database Configuration
DATABASE_URL=postgresql://your_username:your_password@localhost:5432/nepse_bot
DB_USER=your_username
DB_PASSWORD=your_password
4. Set Up PostgreSQL Database
# Connect to PostgreSQL
psql -U postgres

# Create database
CREATE DATABASE nepse_bot;

# Exit PostgreSQL
\q
5. Test Database Connection
python test_connection.py
You should see:

✅ SUCCESS: Database connection is working!
6. Run the Application
python app/main.py
Or using uvicorn directly:

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
7. Access API Documentation
Open your browser and navigate to:

Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
Health Check: http://localhost:8000/health
📁 Project Structure
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
🏗️ Architecture
Modular Design
The application follows a modular architecture with clear separation of concerns:

Configuration Layer (config.py)

Centralized configuration management
Environment-based settings
Type-safe configuration access
Database Layer (database.py)

Connection pooling
Session management
Health checks
Service Layer (services/)

Abstract interfaces for external APIs
Easy to swap implementations
NEPSE API client with retry logic
API Layer (api/)

RESTful endpoints
Request validation
Response formatting
Business Logic (components/)

Strategy components
Signal generation
Risk management
API Client Architecture
The API client uses an abstract base class pattern for flexibility:

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
Benefits:

✅ Easy to switch API providers
✅ Consistent interface across providers
✅ Easy to test with mock implementations
✅ Future-proof design
🔧 Configuration
All configuration is managed through environment variables in .env:

Application Settings
APP_NAME=NEPSE Trading Bot
APP_VERSION=1.0.0
ENVIRONMENT=development
DEBUG=True
Database Settings
DATABASE_URL=postgresql://user:password@localhost:5432/nepse_bot
API Settings
NEPSE_API_BASE_URL=https://www.nepalstock.com.np/api
NEPSE_API_TIMEOUT=30
NEPSE_API_RETRY_ATTEMPTS=3
Trading Settings
DEFAULT_RISK_PERCENTAGE=1.0
MAX_RISK_PERCENTAGE=2.0
DEFAULT_REWARD_RISK_RATIO=2.0
🧪 Testing
Test Database Connection
python test_connection.py
Test API Connection
curl http://localhost:8000/test-api
Run Unit Tests (Coming in Day 21)
pytest
📊 API Endpoints
Core Endpoints
GET / - Root endpoint with API information
GET /health - Health check (database + API)
GET /docs - Swagger UI documentation
GET /redoc - ReDoc documentation
Database Endpoints
GET /db-info - Database connection information
GET /test-db - Test database connection
API Endpoints
GET /test-api - Test NEPSE API connection
GET /config - Get application configuration
Future Endpoints (Coming Soon)
GET /api/v1/signals - List active signals
GET /api/v1/signals/{id} - Get signal details
POST /api/v1/signals/generate - Generate new signals
GET /api/v1/bot-configs - List bot configurations
And more...
🔍 Troubleshooting
Database Connection Issues
Problem: Database connection failed

Solutions:

Check if PostgreSQL is running: pg_isready
Verify database exists: psql -U postgres -l | grep nepse_bot
Check credentials in .env file
Ensure PostgreSQL accepts connections: Check pg_hba.conf
TA-Lib Installation Issues
Problem: Failed to install TA-Lib

Solutions:

macOS: brew install ta-lib
Ubuntu: sudo apt-get install ta-lib
Windows: Download from GitHub
Port Already in Use
Problem: Address already in use

Solution:

# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or use a different port
uvicorn app.main:app --port 8001
📝 Development Roadmap
 Day 1: Environment Setup ✅
 Day 2: Database Foundation
 Day 3: Base Architecture
 Day 4: NEPSE API Client
 Day 5: Data Storage
 Day 6-7: Technical Indicators
 Day 8-14: Strategy Components
 Day 15-16: Signal Generation & Risk Management
 Day 17-19: API Endpoints & Scheduling
 Day 20-21: Testing & Deployment
🤝 Contributing
Contributions are welcome! Please follow these steps:

Fork the repository
Create a feature branch
Make your changes
Write tests
Submit a pull request
📄 License
This project is licensed under the MIT License.

📧 Support
For issues and questions:

Create an issue on GitHub
Check the documentation at /docs
Review the TODO.md file for implementation details
🙏 Acknowledgments
Nepal Stock Exchange (NEPSE) for market data
FastAPI for the excellent web framework
SQLAlchemy for database ORM
TA-Lib for technical analysis
Built with ❤️ for NEPSE traders