# NEPSE Trading Bot - Architecture Documentation

## 🏗️ System Architecture Overview

This document explains the architectural decisions and design patterns used in the NEPSE Trading Bot.

---

## 📐 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                       │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   FastAPI    │  │   Swagger    │  │    ReDoc     │     │
│  │  Endpoints   │  │     UI       │  │              │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      API LAYER (v1)                          │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Signals    │  │    Stocks    │  │   Configs    │     │
│  │   Routes     │  │    Routes    │  │    Routes    │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                    BUSINESS LOGIC LAYER                      │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Sector     │  │  Liquidity   │  │Market Depth  │     │
│  │ Identifier   │  │   Hunter     │  │  Analyzer    │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │ Floorsheet   │  │     Risk     │                        │
│  │  Analyzer    │  │   Manager    │                        │
│  └──────────────┘  └──────────────┘                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      SERVICE LAYER                           │
│                                                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │         BaseAPIClient (Abstract Interface)        │      │
│  └──────────────────────────────────────────────────┘      │
│                            ↓                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │    NEPSE     │  │ Alternative  │  │    Future    │     │
│  │ API Client   │  │ API Client   │  │   Providers  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      DATA LAYER                              │
│                                                              │
│  ┌──────────────────────────────────────────────────┐      │
│  │              SQLAlchemy ORM                       │      │
│  └──────────────────────────────────────────────────┘      │
│                            ↓                                 │
│  ┌──────────────────────────────────────────────────┐      │
│  │              PostgreSQL Database                  │      │
│  │  (bot_configs, stocks, signals, market_data...)  │      │
│  └──────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                   EXTERNAL SERVICES                          │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  NEPSE API   │  │  PostgreSQL  │  │   Scheduler  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Design Patterns Used

### 1. **Dependency Injection Pattern**

**Location:** Throughout the application, especially in FastAPI endpoints

**Purpose:** Manage dependencies and their lifecycles automatically

**Example:**

```python
from fastapi import Depends
from app.database import get_db

@app.get("/items")
def get_items(db: Session = Depends(get_db)):
    # db is automatically injected and cleaned up
    return db.query(Item).all()
```

**Benefits:**

- ✅ Automatic resource management
- ✅ Easy to test (can inject mocks)
- ✅ Loose coupling
- ✅ Clear dependencies

---

### 2. **Abstract Factory Pattern**

**Location:** `app/services/base_api_client.py` and `create_api_client()`

**Purpose:** Create API clients without specifying exact classes

**Example:**

```python
# Abstract interface
class BaseAPIClient(ABC):
    @abstractmethod
    def fetch_market_indices(self): pass

# Concrete implementations
class NepseAPIClient(BaseAPIClient):
    def fetch_market_indices(self):
        # NEPSE implementation
        pass

class AlternativeAPIClient(BaseAPIClient):
    def fetch_market_indices(self):
        # Alternative implementation
        pass

# Factory function
def create_api_client(provider: str) -> BaseAPIClient:
    if provider == "nepse":
        return NepseAPIClient()
    elif provider == "alternative":
        return AlternativeAPIClient()
```

**Benefits:**

- ✅ Easy to add new API providers
- ✅ Client code doesn't depend on concrete classes
- ✅ Consistent interface across providers
- ✅ Easy to switch providers

---

### 3. **Repository Pattern** (Coming in Day 2)

**Location:** Will be in `app/repositories/`

**Purpose:** Abstract data access logic

**Example:**

```python
class StockRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_symbol(self, symbol: str):
        return self.db.query(Stock).filter_by(symbol=symbol).first()

    def get_all(self):
        return self.db.query(Stock).all()
```

**Benefits:**

- ✅ Centralized data access
- ✅ Easy to change database
- ✅ Testable with mocks
- ✅ Reusable queries

---

### 4. **Strategy Pattern** (Coming in Day 8+)

**Location:** Will be in `app/components/`

**Purpose:** Define family of algorithms (trading strategies)

**Example:**

```python
class BaseComponent(ABC):
    @abstractmethod
    def analyze(self, config): pass

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

- ✅ Easy to add new strategies
- ✅ Strategies are interchangeable
- ✅ Each strategy is independent
- ✅ Easy to enable/disable

---

### 5. **Singleton Pattern**

**Location:** `app/config.py` - `settings` instance

**Purpose:** Ensure only one configuration instance exists

**Example:**

```python
# Global settings instance
settings = Settings()

# Used throughout the application
from app.config import settings
```

**Benefits:**

- ✅ Single source of truth
- ✅ Consistent configuration
- ✅ Memory efficient
- ✅ Easy to access

---

### 6. **Builder Pattern** (Coming in Day 15)

**Location:** Will be in signal generation

**Purpose:** Construct complex signal objects step by step

**Example:**

```python
class SignalBuilder:
    def __init__(self):
        self.signal = Signal()

    def set_stock(self, stock):
        self.signal.stock = stock
        return self

    def set_entry_price(self, price):
        self.signal.entry_price = price
        return self

    def build(self):
        return self.signal

# Usage
signal = (SignalBuilder()
    .set_stock("NABIL")
    .set_entry_price(1200)
    .set_stop_loss(1140)
    .build())
```

**Benefits:**

- ✅ Flexible object construction
- ✅ Readable code
- ✅ Immutable objects
- ✅ Validation at build time

---

## 🔧 Key Architectural Decisions

### 1. **Configuration-Driven Architecture**

**Decision:** All parameters stored in database, not code

**Rationale:**

- Easy to create new bot configurations
- No code changes needed for parameter tuning
- Multiple bots can run with different configs
- Configuration history tracked in database

**Implementation:**

```python
# Configuration in database
bot_config = {
    "volume_days": 10,
    "beta_threshold": 1.2,
    "rsi_threshold": 30,
    "fundamental_enabled": True
}

# Components read from config
class SectorIdentifier:
    def analyze(self, config):
        volume_days = config.get("volume_days", 10)
        # Use configuration
```

---

### 2. **Modular Component Design**

**Decision:** Each strategy component is independent

**Rationale:**

- Easy to test components individually
- Components can be reused
- Easy to enable/disable components
- Clear separation of concerns

**Implementation:**

```python
# Each component is independent
sector_result = SectorIdentifier().analyze(config)
liquidity_result = LiquidityHunter().analyze(config)
depth_result = MarketDepthAnalyzer().analyze(config)

# Combine results
signal = combine_results([
    sector_result,
    liquidity_result,
    depth_result
])
```

---

### 3. **Abstract API Client Interface**

**Decision:** Use abstract base class for API clients

**Rationale:**

- Easy to switch API providers
- Consistent interface
- Future-proof design
- Easy to test with mocks

**Implementation:**

```python
# Define interface
class BaseAPIClient(ABC):
    @abstractmethod
    def fetch_market_indices(self): pass

# Use factory
client = create_api_client(provider="nepse")
# Can easily switch to "alternative"
```

---

### 4. **Database Connection Pooling**

**Decision:** Use SQLAlchemy with connection pooling

**Rationale:**

- Better performance
- Automatic connection management
- Handle concurrent requests
- Prevent connection leaks

**Implementation:**

```python
engine = create_engine(
    database_url,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True
)
```

---

### 5. **Type-Safe Configuration**

**Decision:** Use Pydantic Settings for configuration

**Rationale:**

- Type validation at startup
- Clear error messages
- IDE autocomplete support
- Documentation in code

**Implementation:**

```python
class Settings(BaseSettings):
    database_url: str = Field(...)
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
```

---

## 📦 Module Organization

### app/config.py

**Purpose:** Configuration management
**Responsibilities:**

- Load environment variables
- Validate settings
- Provide type-safe access
- Support multiple environments

### app/database.py

**Purpose:** Database connection management
**Responsibilities:**

- Create database engine
- Manage connection pool
- Provide session factory
- Health checks

### app/services/

**Purpose:** External service integrations
**Responsibilities:**

- API client implementations
- Abstract interfaces
- Retry logic
- Error handling

### app/models/

**Purpose:** Database models
**Responsibilities:**

- Define table schemas
- Relationships
- Constraints
- Indexes

### app/api/

**Purpose:** API endpoints
**Responsibilities:**

- Request validation
- Response formatting
- Error handling
- Documentation

### app/components/

**Purpose:** Trading strategy components
**Responsibilities:**

- Sector analysis
- Pattern detection
- Signal generation
- Risk calculation

### app/indicators/

**Purpose:** Technical indicators
**Responsibilities:**

- EMA, RSI, ATR calculations
- Volume analysis
- Pattern recognition
- Beta calculation

### app/utils/

**Purpose:** Utility functions
**Responsibilities:**

- Helper functions
- Common operations
- Data transformations
- Validators

---

## 🔄 Data Flow

### 1. **Signal Generation Flow**

```
User Request
    ↓
API Endpoint (/api/v1/signals/generate)
    ↓
Signal Generator
    ↓
Load Bot Configuration (from database)
    ↓
Initialize Components
    ↓
Fetch Market Data (via API Client)
    ↓
Component 1: Sector Identifier
    ↓
Component 2: Liquidity Hunter
    ↓
Component 3: Market Depth Analyzer
    ↓
Component 4: Floorsheet Analyzer
    ↓
Component 5: Risk Manager
    ↓
Combine Results & Calculate Confidence
    ↓
Save Signal (to database)
    ↓
Return Signal (to user)
```

### 2. **Configuration Update Flow**

```
User Request
    ↓
API Endpoint (/api/v1/bot-configs/{id})
    ↓
Validate Configuration
    ↓
Update Database
    ↓
Notify Scheduler (if needed)
    ↓
Return Updated Config
```

### 3. **Scheduled Task Flow**

```
APScheduler Trigger
    ↓
Task Function (e.g., fetch_market_data)
    ↓
API Client (fetch data)
    ↓
Process Data
    ↓
Save to Database
    ↓
Log Result
```

---

## 🔐 Security Considerations

### 1. **Environment Variables**

- ✅ Sensitive data in .env (not committed)
- ✅ .env.example for reference
- ✅ Validation at startup

### 2. **Database Security**

- ✅ Connection pooling
- ✅ Prepared statements (SQLAlchemy)
- ✅ No raw SQL queries

### 3. **API Security** (Future)

- 🔄 JWT authentication
- 🔄 Rate limiting
- 🔄 CORS configuration
- 🔄 Input validation

### 4. **Error Handling**

- ✅ No sensitive data in error messages
- ✅ Proper logging
- ✅ Generic error responses

---

## 📊 Performance Considerations

### 1. **Database**

- ✅ Connection pooling (5 connections, 10 overflow)
- ✅ Indexes on frequently queried columns
- ✅ Efficient queries
- 🔄 Query optimization

### 2. **API Calls**

- ✅ Retry logic with exponential backoff
- ✅ Timeout configuration
- 🔄 Caching (future)
- 🔄 Rate limiting (future)

### 3. **Caching** (Future)

- 🔄 Redis for market data
- 🔄 In-memory cache for configs
- 🔄 Cache invalidation strategy

### 4. **Async Operations** (Future)

- 🔄 Async API calls
- 🔄 Async database queries
- 🔄 Background tasks

---

## 🧪 Testing Strategy

### 1. **Unit Tests**

- Test individual functions
- Mock external dependencies
- Test edge cases
- Fast execution

### 2. **Integration Tests**

- Test component interactions
- Use test database
- Test API endpoints
- Test data flow

### 3. **End-to-End Tests**

- Test complete workflows
- Test signal generation
- Test configuration updates
- Test scheduled tasks

---

## 🚀 Scalability Considerations

### 1. **Horizontal Scaling**

- Stateless API design
- Database connection pooling
- Load balancer ready

### 2. **Vertical Scaling**

- Efficient algorithms
- Optimized queries
- Resource monitoring

### 3. **Future Enhancements**

- Message queue (Celery/RabbitMQ)
- Microservices architecture
- Distributed caching
- Container orchestration (Kubernetes)

---

## 📝 Code Quality Standards

### 1. **Type Hints**

- All functions have type hints
- Return types specified
- Parameter types specified

### 2. **Documentation**

- Docstrings for all classes/functions
- Inline comments for complex logic
- README and guides

### 3. **Error Handling**

- Try-except blocks
- Proper logging
- User-friendly messages

### 4. **Code Style**

- PEP 8 compliance
- Consistent naming
- Clear structure

---

## 🔄 Future Architecture Improvements

### 1. **Microservices** (Optional)

- Separate services for each component
- API gateway
- Service discovery

### 2. **Event-Driven Architecture**

- Message queue for events
- Async processing
- Event sourcing

### 3. **Caching Layer**

- Redis for market data
- Cache warming
- Cache invalidation

### 4. **Monitoring & Observability**

- Prometheus metrics
- Grafana dashboards
- Distributed tracing
- Log aggregation

---

**This architecture is designed to be:**

- ✅ Modular and maintainable
- ✅ Scalable and performant
- ✅ Testable and reliable
- ✅ Flexible and extensible
- ✅ Well-documented and clear

---

**Built with best practices and industry standards! 🏗️**
