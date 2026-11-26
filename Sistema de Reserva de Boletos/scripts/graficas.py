"""Genera las graficas y la tabla LaTeX del informe de rendimiento a partir de resultados/resumen.json.

    .venv/bin/python scripts/graficas.py

Escribe Informe proyecto/Images/rend_*.png y Informe proyecto/tabla_resultados.tex.
(matplotlib solo se necesita para esto: .venv/bin/pip install matplotlib)
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
INFORME = os.path.join(RAIZ, "..", "Informe proyecto")
datos = json.load(open(os.path.join(RAIZ, "resultados", "resumen.json"), encoding="utf-8"))
por = {(d["arquitectura"], d["clientes"]): d for d in datos}
NS = [3, 5, 8]
COLORES = {"base": "#c0504d", "escalable": "#1f6f8b"}


def grafica(clave, campo, titulo, etiqueta_y, archivo, barras_error):
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    for arq in ("base", "escalable"):
        y = [por[(arq, n)][clave][campo] for n in NS]
        err = [por[(arq, n)][clave]["desv_ms"] for n in NS] if barras_error else None
        ax.errorbar(NS, y, yerr=err, marker="o", capsize=3, label=arq.capitalize(), color=COLORES[arq])
    ax.set_title(titulo, fontsize=10)
    ax.set_xlabel("Clientes simultáneos")
    ax.set_ylabel(etiqueta_y)
    ax.set_xticks(NS)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(INFORME, "Images", archivo), dpi=200)
    plt.close(fig)


grafica("consulta", "media_ms", "Tiempo de respuesta: consulta", "Media ± desv. est. (ms)", "rend_consulta_ms.png", True)
grafica("reserva", "media_ms", "Tiempo de respuesta: reserva", "Media ± desv. est. (ms)", "rend_reserva_ms.png", True)
grafica("consulta", "tpm", "Throughput: consultas", "Transacciones por minuto (TPM)", "rend_consulta_tpm.png", False)
grafica("reserva", "tpm", "Throughput: reservas", "Transacciones por minuto (TPM)", "rend_reserva_tpm.png", False)

filas = []
for n in NS:
    for arq in ("base", "escalable"):
        d = por[(arq, n)]
        filas.append(f"{n} & {arq.capitalize()} & {d['consulta']['media_ms']:.1f} $\\pm$ {d['consulta']['desv_ms']:.1f} & "
                     f"{d['reserva']['media_ms']:.1f} $\\pm$ {d['reserva']['desv_ms']:.1f} & "
                     f"{d['consulta']['tpm']:,.0f} & {d['reserva']['tpm']:,.0f} & {d['errores_tecnicos']} \\\\".replace(",", "\\,"))
    filas.append("\\midrule")
filas.pop()
tabla = ("\\begin{tabular}{@{}rlrrrrr@{}}\n\\toprule\n"
         "Clientes & Arquitectura & Consulta (ms) & Reserva (ms) & TPM consulta & TPM reserva & Errores \\\\\n\\midrule\n"
         + "\n".join(filas) + "\n\\bottomrule\n\\end{tabular}\n")
open(os.path.join(INFORME, "tabla_resultados.tex"), "w", encoding="utf-8").write(tabla)

# mejora relativa para el analisis
for clave, nombre in (("consulta", "consulta"), ("reserva", "reserva")):
    for n in NS:
        b, e = por[("base", n)][clave], por[("escalable", n)][clave]
        print(f"{nombre} {n} clientes: tiempo {b['media_ms']} -> {e['media_ms']} ms "
              f"({(1 - e['media_ms'] / b['media_ms']) * 100:+.0f}%), TPM {b['tpm']} -> {e['tpm']} ({(e['tpm'] / b['tpm'] - 1) * 100:+.0f}%)")
