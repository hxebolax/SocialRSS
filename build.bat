@echo off

:: SocialRSS Accessible Suite - Script de Administración y Compilación

:: Totalmente accesible y en español.



chcp 65001 > nul

setlocal enabledelayedexpansion



:MENU

cls

echo =================================================================

echo        SocialRSS Accessible Suite - Menú de Administración

echo =================================================================

echo 1. Crear entorno virtual (.venv)

echo 2. Instalar / Actualizar dependencias (requirements.txt y basskit)

echo 3. Ejecutar aplicación desde código fuente (main.py)

echo 4. Compilar aplicación con cx_Freeze (setup.py)

echo 5. Limpiar temporales, build y cachés

echo 6. Extraer cadenas a plantilla de traducción (.pot)

echo 7. Compilar archivos de traducción (.po a .mo)

echo 8. Ejecutar pruebas unitarias (pytest)

echo 9. Abrir carpeta de datos de usuario (data)

echo 10. Abrir carpeta de registros (logs)

echo 11. Ejecutar diagnóstico del entorno

echo 12. Salir

echo =================================================================

echo.

set /p OPCION="Seleccione una opción [1-12] y presione Enter: "



if "%OPCION%"=="1" goto ENTORNO

if "%OPCION%"=="2" goto DEPENDENCIAS

if "%OPCION%"=="3" goto EJECUTAR

if "%OPCION%"=="4" goto COMPILAR

if "%OPCION%"=="5" goto LIMPIAR

if "%OPCION%"=="6" goto EXTRAER_POT

if "%OPCION%"=="7" goto COMPILAR_MO

if "%OPCION%"=="8" goto PRUEBAS

if "%OPCION%"=="9" goto CARPETA_DATA

if "%OPCION%"=="10" goto CARPETA_LOGS

if "%OPCION%"=="11" goto DIAGNOSTICO

if "%OPCION%"=="12" goto FIN

goto OPCION_INVALIDA



:ENTORNO

echo.

echo === Creando entorno virtual (.venv) ===

python -m venv .venv

if %ERRORLEVEL% equ 0 (

	echo Entorno virtual creado con éxito en la carpeta .venv.

) else (

	echo Error al crear el entorno virtual. Asegúrese de tener Python instalado y en su PATH.

)

pause

goto MENU



:DEPENDENCIAS

echo.

echo === Activando entorno virtual e instalando dependencias ===

if not exist .venv\Scripts\activate.bat (

	echo No se encontró el entorno virtual. Creándolo primero...

	python -m venv .venv

)

call .venv\Scripts\activate.bat

echo Actualizando pip...

python -m pip install --upgrade pip

echo Instalando dependencias desde requirements.txt...

pip install -r requirements.txt -r requirements-dev.txt

if %ERRORLEVEL% equ 0 (

	echo.

	echo Todas las dependencias e instalador basskit se cargaron con éxito.

) else (

	echo.

	echo Hubo un error al instalar algunas dependencias.

)

REM Cifrado E2EE de Matrix (python-olm): requiere compilarse una vez (no va en
REM requirements.txt). Si falta, ofrecer instalarlo llamando a su script.
python -c "import olm" >nul 2>&1
if %ERRORLEVEL% neq 0 (
	echo.
	echo AVISO: el cifrado de Matrix ^(python-olm^) no esta instalado en el entorno.
	echo Sin el, las salas cifradas de Matrix no se podran leer en la build.
	set /p RESP_OLM="¿Compilarlo e instalarlo ahora? Requiere Visual Studio C++ (S/N): "
	if /i "%RESP_OLM%"=="S" call "%~dp0tools\build_python_olm.bat"
) else (
	echo Cifrado de Matrix ^(python-olm^): ya instalado.
)

pause

goto MENU



:EJECUTAR

echo.

echo === Iniciando SocialRSS Accessible Suite desde código fuente ===

if exist .venv\Scripts\activate.bat (

	call .venv\Scripts\activate.bat

)

python main.py

if %ERRORLEVEL% neq 0 (

	echo.

	echo La aplicación terminó con un código de error: %ERRORLEVEL%

)

pause

goto MENU



:COMPILAR

echo.

echo === Compilando aplicación con cx_Freeze ===

if exist .venv\Scripts\activate.bat (

	call .venv\Scripts\activate.bat

)

if not exist setup.py (

	echo Error: setup.py no existe en la raíz del proyecto.

	pause

	goto MENU

)

python setup.py build

echo.

if exist build\SocialRSS (

	echo Compilación terminada con éxito. Carpeta: build\SocialRSS

	echo.

	echo === Comprimiendo en .zip con la versión ===

	python comprimir_build.py

) else (

	echo Error durante la compilación: no se generó la carpeta build\SocialRSS.

)

pause

goto MENU



:LIMPIAR

echo.

echo === Limpiando temporales, compilados y cachés ===

if exist build rmdir /s /q build

if exist dist rmdir /s /q dist

if exist .pytest_cache rmdir /s /q .pytest_cache

for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"

echo Limpieza realizada con éxito.

pause

goto MENU



:EXTRAER_POT

echo.

echo === Generando plantilla de traducción (.pot) ===

if exist .venv\Scripts\activate.bat (

	call .venv\Scripts\activate.bat

)

:: Crear estructura de directorios locale si no existe

if not exist data\locales mkdir data\locales

echo Buscando archivos python y extrayendo cadenas _()...

python -c "import os, polib; print('Herramientas de traducción cargadas')" 2>nul

if %ERRORLEVEL% neq 0 (

	echo Instalando dependencias necesarias para traducciones...

	pip install polib

)

:: Ejecutar script interno de generación de traducción

python tools\gen_pot.py

pause

goto MENU



:COMPILAR_MO

echo.

echo === Compilando archivos .po a .mo ===

if exist .venv\Scripts\activate.bat (

	call .venv\Scripts\activate.bat

)

python tools\compile_po.py

pause

goto MENU



:PRUEBAS

echo.

echo === Ejecutando pruebas unitarias ===

if exist .venv\Scripts\activate.bat (

	call .venv\Scripts\activate.bat

)

python -m pytest tests -q

if %ERRORLEVEL% equ 0 (

	echo.

	echo ¡Todas las pruebas han pasado con éxito!

) else (

	echo.

	echo Hubo fallos en las pruebas unitarias.

)

pause

goto MENU



:CARPETA_DATA

echo.

echo === Abriendo la carpeta de datos de usuario ===

if not exist data mkdir data

start explorer data

goto MENU



:CARPETA_LOGS

echo.

echo === Abriendo la carpeta de registros ===

if not exist data\logs mkdir data\logs

start explorer data\logs

goto MENU



:DIAGNOSTICO

echo.

echo === Diagnóstico del entorno ===

echo Versión de Python activa:

python --version

echo.

echo Buscando lectores de pantalla activos:

tasklist /fi "imagename eq nvda.exe" | findstr /i "nvda.exe" >nul

if %ERRORLEVEL% equ 0 (

	echo NVDA está ejecutándose actualmente.

) else (

	echo NVDA no se detecta en ejecución.

)

tasklist /fi "imagename eq jaws.exe" | findstr /i "jaws.exe" >nul

if %ERRORLEVEL% equ 0 (

	echo JAWS está ejecutándose actualmente.

) else (

	echo JAWS no se detecta en ejecución.

)

echo.

echo Verificando si basskit está disponible en el entorno virtual...

if exist .venv\Scripts\activate.bat (

	call .venv\Scripts\activate.bat

)

python tools\diag_basskit.py

pause

goto MENU



:OPCION_INVALIDA

echo.

echo Opción inválida. Intente de nuevo.

pause

goto MENU



:FIN

echo.

echo ¡Hasta luego! Gracias por usar SocialRSS Accessible Suite.

pause

