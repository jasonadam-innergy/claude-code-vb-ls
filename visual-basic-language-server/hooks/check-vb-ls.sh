#!/bin/bash

# Check if vb-ls is installed and available in PATH

if command -v vb-ls &> /dev/null; then
    exit 0
fi

# Check if dotnet is available
if command -v dotnet &> /dev/null; then
    echo "[vb-ls] Installing vb-ls via dotnet..."
    dotnet tool install --global vb-ls

    if command -v vb-ls &> /dev/null; then
        echo "[vb-ls] Installed successfully"
        exit 0
    fi
fi

# Manual instructions
echo "[vb-ls] vb-ls is not installed."
echo "          Install: dotnet tool install --global vb-ls"

exit 0
