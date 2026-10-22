# -*- coding: utf-8 -*-
"""Comprueba que basskit está disponible e imprime su diagnóstico. Lo llama build.bat
(diagnóstico del entorno). Se ejecuta como archivo .py porque cmd no admite 'python -c' multilínea.
"""

def main():
    try:
        import basskit
        print("basskit está instalado correctamente.")
        try:
            basskit.imprimir_diagnostico()
        except Exception as e:
            print("basskit importa, pero falló el diagnóstico:", e)
    except Exception as e:
        print("Error al importar o diagnosticar basskit:", e)


if __name__ == "__main__":
    main()
