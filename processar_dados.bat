@echo off
:: Altera o diretório atual para a pasta onde o arquivo .bat está localizado
cd /d "%~dp0"

echo [INFO] Iniciando o processamento do primeiro script...
py gerar_insights_semanais.py "kpis_semanais.csv"

echo.
echo [INFO] Iniciando o processamento do segundo script...
py classificar_sentimentos.py "avaliacoes_clientes.csv"

echo.
echo [INFO] Processamento de ambos os scripts finalizado com sucesso!
pause