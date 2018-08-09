REM Start the Google App Engine Development Server for Python
set GOOGLE_APPLICATION_CREDENTIALS=resources\netskrafl-0dd9fbdf9ab3.json
set CLOUD_SDK="C:\Users\Lenovo\AppData\Local\Google\Cloud SDK\google-cloud-sdk"
set "PYTHONPATH=%CLOUD_SDK%\platform\google_appengine"
set "DEV_APPSERVER=%CLOUD_SDK%\bin\dev_appserver.py"
python %DEV_APPSERVER% --port=8080 --admin_port=8000 --host=0.0.0.0 --enable_host_checking=False app.yaml skraflstats.yaml
