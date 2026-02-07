@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python scripts\test_nested_structure.py
pause
