#!/bin/bash

# NEPSE Trading Bot - Virtual Environment Setup Script
# This script automates the creation of Python virtual environment
# and installation of dependencies

echo "=========================================="
echo "NEPSE Trading Bot - Environment Setup"
echo "=========================================="
echo ""

# Check if Python 3.10+ is installed
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
required_version="3.10"

if [ -z "$python_version" ]; then
    echo "❌ Python 3 is not installed!"
    echo "Please install Python 3.10 or higher"
    exit 1
fi

# Compare versions
if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ Python version $python_version is too old!"
    echo "Please install Python 3.10 or higher"
    exit 1
fi

echo "✅ Python $python_version detected"
echo ""

# Create virtual environment
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo "⚠️  Virtual environment already exists"
    read -p "Do you want to recreate it? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Removing existing virtual environment..."
        rm -rf venv
        python3 -m venv venv
        echo "✅ Virtual environment recreated"
    else
        echo "Using existing virtual environment"
    fi
else
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi
echo ""

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate
echo "✅ Virtual environment activated"
echo ""

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip
echo "✅ pip upgraded"
echo ""

# Install dependencies
echo "Installing dependencies..."
echo "This may take a few minutes..."
pip install -r requirements.txt

if [ $? -eq 0 ]; then
    echo "✅ Dependencies installed successfully"
else
    echo "❌ Failed to install some dependencies"
    echo ""
    echo "Note: TA-Lib requires system-level installation"
    echo "On macOS: brew install ta-lib"
    echo "On Ubuntu: sudo apt-get install ta-lib"
    echo ""
    echo "You can continue without TA-Lib for now"
fi
echo ""

# Create logs directory
echo "Creating logs directory..."
mkdir -p logs
echo "✅ Logs directory created"
echo ""

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found"
    echo "Copying .env.example to .env..."
    cp .env.example .env
    echo "✅ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Please update .env file with your database credentials"
else
    echo "✅ .env file exists"
fi
echo ""

echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Update .env file with your database credentials"
echo "2. Make sure PostgreSQL is installed and running"
echo "3. Create database: psql -U postgres -c 'CREATE DATABASE nepse_bot;'"
echo "4. Test connection: python test_connection.py"
echo "5. Run application: python app/main.py"
echo ""
echo "To activate virtual environment in future:"
echo "  source venv/bin/activate"
echo ""
echo "To deactivate virtual environment:"
echo "  deactivate"
echo ""
echo "=========================================="
