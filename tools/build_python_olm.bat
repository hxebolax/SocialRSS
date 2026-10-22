@echo off
REM ============================================================================
REM Compila e instala python-olm (el motor de cifrado E2EE de Matrix) en Windows.
REM Necesario UNA VEZ en la maquina de desarrollo/build; los usuarios finales
REM reciben la build congelada con la DLL dentro (no compilan nada).
REM
REM Requisitos: Visual Studio (Community) con "Desarrollo para escritorio con C++"
REM y CMake en el PATH. Receta descubierta el 2026-07-02 (ver memoria del proyecto):
REM  1) vcvars64: entorno del compilador MSVC.
REM  2) CMAKE_POLICY_VERSION_MINIMUM=3.5: libolm declara un minimo antiguo que
REM     CMake 4 ya no acepta sin esta variable.
REM  3) DISTUTILS_USE_SDK/MSSdk: setuptools no reconoce Visual Studio 18 (muy
REM     nuevo); con esto usa el compilador del entorno.
REM  4) CMAKE_GENERATOR=NMake Makefiles: deja olm.lib en build\ (donde python-olm
REM     la busca), no en build\Release\.
REM  5) CMAKE_BUILD_TYPE=Release: sin esto libolm sale en Debug (CRT de depuracion)
REM     y el enlace final falla con __imp__calloc_dbg.
REM ============================================================================
setlocal

set VCVARS="C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
if not exist %VCVARS% (
    for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSDIR=%%i
    set VCVARS="%VSDIR%\VC\Auxiliary\Build\vcvars64.bat"
)
call %VCVARS% >nul 2>&1
if errorlevel 1 (
    echo ERROR: no se encontro Visual Studio con las herramientas de C++.
    exit /b 1
)

set CMAKE_POLICY_VERSION_MINIMUM=3.5
set DISTUTILS_USE_SDK=1
set MSSdk=1
set CMAKE_GENERATOR=NMake Makefiles
set CMAKE_BUILD_TYPE=Release

"%~dp0..\.venv\Scripts\pip.exe" install python-olm --no-cache-dir
if errorlevel 1 (
    echo ERROR: fallo la compilacion de python-olm.
    exit /b 1
)
"%~dp0..\.venv\Scripts\pip.exe" install "matrix-nio[e2e]"
echo.
echo LISTO: python-olm y matrix-nio[e2e] instalados. Verificando...
"%~dp0..\.venv\Scripts\python.exe" -c "import olm; a=olm.Account(); print('olm OK:', list(a.identity_keys.keys()))"
endlocal
