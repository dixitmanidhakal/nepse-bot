"""
Configuration Management Module

This module handles all application configuration using Pydantic Settings.
It loads environment variables from .env file and provides type-safe access.

Architecture Benefits:
- Centralized configuration management
- Type validation for all settings
- Easy to extend with new configuration sections
- Environment-specific settings support
"""

from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application Settings
    
    All configuration is loaded from environment variables or .env file.
    This provides a single source of truth for all application settings.
    """
    
    # Application Settings
    app_name: str = Field(default="NEPSE Trading Bot", description="Application name")
    app_version: str = Field(default="1.0.0", description="Application version")
    environment: str = Field(default="development", description="Environment (development/production)")
    debug: bool = Field(default=True, description="Debug mode")
    
    # Server Configuration
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    
    # Database Configuration
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/nepse_bot",
        description="PostgreSQL connection string"
    )
    
    # Alternative database settings (for manual construction)
    db_host: str = Field(default="localhost", description="Database host")
    db_port: int = Field(default=5432, description="Database port")
    db_name: str = Field(default="nepse_bot", description="Database name")
    db_user: str = Field(default="postgres", description="Database user")
    db_password: str = Field(default="postgres", description="Database password")
    
    # NEPSE API Configuration
    nepse_api_base_url: str = Field(
        default="https://www.nepalstock.com.np/api",
        description="Base URL for nepalstock.com internal API (scraped)"
    )
    nepse_api_timeout: int = Field(default=30, description="API request timeout in seconds")
    nepse_api_retry_attempts: int = Field(default=3, description="Number of retry attempts for failed requests")
    
    # Alternative API Configuration (for future flexibility)
    alternative_api_base_url: Optional[str] = Field(
        default=None,
        description="Alternative API base URL (for switching providers)"
    )
    alternative_api_key: Optional[str] = Field(
        default=None,
        description="Alternative API key"
    )

    # Quant Terminal SQLite DB (read-only historical fallback)
    # Points to nepse_data.db produced by nepse-quant-terminal setup_data.py.
    # If set and the file exists, historical OHLCV falls back to this DB
    # when the live scraper returns empty (e.g., geo-block).
    quant_terminal_db_path: Optional[str] = Field(
        default=None,
        description="Absolute path to nepse-quant-terminal's nepse_data.db SQLite file"
    )
    
    # Trading Configuration
    default_risk_percentage: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="Default risk per trade (1-2% recommended)"
    )
    max_risk_percentage: float = Field(
        default=2.0,
        ge=0.1,
        le=5.0,
        description="Maximum risk per trade"
    )
    default_reward_risk_ratio: float = Field(
        default=2.0,
        ge=1.0,
        description="Default reward:risk ratio"
    )
    
    # Scheduler Configuration
    enable_scheduler: bool = Field(default=True, description="Enable background task scheduler")
    market_data_fetch_interval: int = Field(default=5, description="Market data fetch interval (minutes)")
    sector_analysis_interval: int = Field(default=15, description="Sector analysis interval (minutes)")
    stock_screening_interval: int = Field(default=15, description="Stock screening interval (minutes)")
    pattern_detection_interval: int = Field(default=15, description="Pattern detection interval (minutes)")
    market_depth_interval: int = Field(default=1, description="Market depth analysis interval (minutes)")
    floorsheet_analysis_interval: int = Field(default=5, description="Floorsheet analysis interval (minutes)")
    signal_generation_interval: int = Field(default=15, description="Signal generation interval (minutes)")
    
    # Logging Configuration
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: str = Field(default="logs/nepse_bot.log", description="Log file path")
    
    # CORS Configuration
    cors_origins: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173", "http://localhost:8000"],
        description="Allowed CORS origins"
    )
    
    # Security Configuration
    secret_key: str = Field(
        default="dev-secret-key-change-in-production",
        description="Secret key for JWT tokens"
    )
    algorithm: str = Field(default="HS256", description="JWT algorithm")
    access_token_expire_minutes: int = Field(default=30, description="Access token expiration time")
    
    # Pydantic Settings Configuration
    model_config = SettingsConfigDict(
        env_file=".env",  # Load from .env file
        env_file_encoding="utf-8",
        case_sensitive=False,  # Environment variables are case-insensitive
        extra="ignore"  # Ignore extra fields in .env
    )
    
    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """
        Validate database URL format
        
        Ensures the database URL starts with postgresql://
        """
        if not v.startswith("postgresql://"):
            raise ValueError("Database URL must start with postgresql://")
        return v
    
    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """
        Validate log level
        
        Ensures log level is one of the standard Python logging levels
        """
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}")
        return v_upper
    
    def get_database_url(self) -> str:
        """
        Get database URL
        
        Returns the configured database URL. This method can be extended
        to support different database providers in the future.
        
        Returns:
            str: PostgreSQL connection string
        """
        return self.database_url
    
    def get_api_base_url(self, provider: str = "nepse") -> str:
        """
        Get API base URL for specified provider
        
        This method provides flexibility to switch between different API providers.
        Currently supports NEPSE API, but can be extended for alternative providers.
        
        Args:
            provider: API provider name ("nepse" or "alternative")
            
        Returns:
            str: Base URL for the specified API provider
            
        Raises:
            ValueError: If provider is not supported
        """
        if provider == "nepse":
            return self.nepse_api_base_url
        elif provider == "alternative" and self.alternative_api_base_url:
            return self.alternative_api_base_url
        else:
            raise ValueError(f"Unsupported API provider: {provider}")
    
    def is_production(self) -> bool:
        """
        Check if running in production environment
        
        Returns:
            bool: True if environment is production
        """
        return self.environment.lower() == "production"
    
    def is_development(self) -> bool:
        """
        Check if running in development environment
        
        Returns:
            bool: True if environment is development
        """
        return self.environment.lower() == "development"


# Global settings instance
# This is imported throughout the application for accessing configuration
settings = Settings()


# Convenience function to get settings
def get_settings() -> Settings:
    """
    Get application settings
    
    This function can be used for dependency injection in FastAPI endpoints.
    
    Returns:
        Settings: Application settings instance
    """
    return settings
