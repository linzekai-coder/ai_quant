@echo off
cd /d D:\work\lzk\ai_quant
echo === Starting update at %DATE% %TIME% === > update_log.txt
venv\Scripts\python.exe data\update_data.py >> update_log.txt 2>&1
echo === Finished at %DATE% %TIME% === >> update_log.txt
type update_log.txt
