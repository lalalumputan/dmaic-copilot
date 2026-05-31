"""
Mesin chart deterministik bersama (NO LLM) — dipakai fase Measure & Control.

Semua perhitungan murni statistik (numpy/pandas). Visual digambar dengan
matplotlib sehingga identik antara preview Streamlit dan dokumen Word.

API utama:
    recommend_chart_type(context, values, phase) -> str
    build_chart(values, chart_type, **kw)        -> dict (payload seragam)
    build_figure(chart_dict, title, y_label)     -> matplotlib Figure
    render_chart_streamlit(chart_dict, ...)       -> gambar di Streamlit
    chart_to_png_b64(chart_dict, ...)             -> base64 PNG untuk Word

Fungsi compute individual juga diekspor bila perlu dipakai langsung:
    compute_imr, compute_individuals, compute_run, compute_histogram,
    compute_pareto, compute_box
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
import numpy as np

# Konstanta SPC untuk Moving Range n=2
_E2 = 2.66      # 3 / d2 (d2 = 1.128)
_D4 = 3.267
_D3 = 0.0

# Daftar jenis chart yang didukung (untuk selectbox UI)
CHART_TYPES = ["imr", "individuals", "run", "histogram", "pareto", "box"]
CHART_LABELS = {
    "imr":         "I-MR Chart",
    "individuals": "Individuals (I) Chart",
    "run":         "Run Chart",
    "histogram":   "Histogram + Spec",
    "pareto":      "Pareto / Bar",
    "box":         "Box Plot",
}


# ======================================================
# Helpers
# ======================================================
def _clean(values: Sequence[Any]) -> List[float]:
    out: List[float] = []
    for v in (values or []):
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(f):
            out.append(f)
    return out


def _round(x: Any, n: int = 4) -> Any:
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return x


# ======================================================
# COMPUTE (deterministik)
# ======================================================
def compute_individuals(values: Sequence[Any]) -> Dict[str, Any]:
    """I-chart sederhana: center = mean, UCL/LCL = mean ± 3σ."""
    arr = _clean(values)
    if len(arr) < 2:
        return {"available": False, "chart_type": "individuals",
                "reason": "Data belum cukup (min. 2 titik)."}
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))
    return {
        "available": True, "chart_type": "individuals",
        "center": _round(mean), "ucl": _round(mean + 3 * std),
        "lcl": _round(mean - 3 * std), "std": _round(std),
        "n": len(arr), "values": [_round(v) for v in arr],
        "note": "I-chart (mean ± 3σ).",
    }


def compute_imr(values: Sequence[Any]) -> Dict[str, Any]:
    """I-MR proper: Individuals + Moving Range pakai MR-bar & konstanta SPC."""
    arr = _clean(values)
    if len(arr) < 2:
        return {"available": False, "chart_type": "imr",
                "reason": "Data belum cukup (min. 2 titik)."}
    mr = [abs(arr[i] - arr[i - 1]) for i in range(1, len(arr))]
    mr_bar = float(np.mean(mr)) if mr else 0.0
    center_i = float(np.mean(arr))
    return {
        "available": True, "chart_type": "imr",
        "n": len(arr),
        "i": {
            "values": [_round(v) for v in arr],
            "center": _round(center_i),
            "ucl": _round(center_i + _E2 * mr_bar),
            "lcl": _round(center_i - _E2 * mr_bar),
        },
        "mr": {
            "values": [_round(v) for v in mr],
            "center": _round(mr_bar),
            "ucl": _round(_D4 * mr_bar),
            "lcl": _round(_D3 * mr_bar),
        },
        # alias level-atas agar kompatibel dengan pembaca lama
        "center": _round(center_i),
        "ucl": _round(center_i + _E2 * mr_bar),
        "lcl": _round(center_i - _E2 * mr_bar),
        "values": [_round(v) for v in arr],
        "note": "I-MR chart (MR-bar × konstanta SPC: E2=2.66, D4=3.267).",
    }


def compute_run(values: Sequence[Any]) -> Dict[str, Any]:
    """Run chart: deret nilai + garis median."""
    arr = _clean(values)
    if len(arr) < 2:
        return {"available": False, "chart_type": "run",
                "reason": "Data belum cukup (min. 2 titik)."}
    return {
        "available": True, "chart_type": "run",
        "values": [_round(v) for v in arr],
        "median": _round(float(np.median(arr))),
        "n": len(arr), "note": "Run chart (garis + median).",
    }


def compute_histogram(values: Sequence[Any], lsl: Optional[float] = None,
                      usl: Optional[float] = None, target: Optional[float] = None,
                      bins: int = 0) -> Dict[str, Any]:
    """Histogram + (opsional) spec limit & kapabilitas Cp/Cpk."""
    arr = _clean(values)
    if len(arr) < 2:
        return {"available": False, "chart_type": "histogram",
                "reason": "Data belum cukup (min. 2 titik)."}
    a = np.asarray(arr, dtype=float)
    if not bins or bins < 1:
        bins = max(5, min(20, int(round(np.sqrt(len(a))))))
    counts, edges = np.histogram(a, bins=bins)
    mean = float(np.mean(a))
    std = float(np.std(a, ddof=1))
    cp = cpk = None
    if std > 0 and lsl is not None and usl is not None:
        cp = (float(usl) - float(lsl)) / (6 * std)
        cpk = min(float(usl) - mean, mean - float(lsl)) / (3 * std)
    return {
        "available": True, "chart_type": "histogram",
        "counts": [int(c) for c in counts],
        "bin_edges": [_round(e) for e in edges],
        "mean": _round(mean), "std": _round(std),
        "lsl": (_round(lsl) if lsl is not None else None),
        "usl": (_round(usl) if usl is not None else None),
        "target": (_round(target) if target is not None else None),
        "cp": (_round(cp) if cp is not None else None),
        "cpk": (_round(cpk) if cpk is not None else None),
        "n": len(arr), "values": [_round(v) for v in arr],
        "note": "Histogram distribusi + spec/kapabilitas.",
    }


def compute_pareto(labels: Sequence[Any], counts: Sequence[Any]) -> Dict[str, Any]:
    """Pareto: kategori diurutkan desc + persentase kumulatif."""
    pairs = []
    for l, c in zip(labels or [], counts or []):
        try:
            cv = float(c)
        except (TypeError, ValueError):
            continue
        pairs.append((str(l), cv))
    if not pairs:
        return {"available": False, "chart_type": "pareto",
                "reason": "Butuh pasangan kategori & jumlah."}
    pairs.sort(key=lambda x: x[1], reverse=True)
    total = sum(c for _, c in pairs) or 1.0
    cum = 0.0
    rows = []
    for l, c in pairs:
        cum += c
        rows.append({"label": l, "count": _round(c),
                     "cum_pct": _round(100.0 * cum / total, 2)})
    return {
        "available": True, "chart_type": "pareto",
        "labels": [r["label"] for r in rows],
        "counts": [r["count"] for r in rows],
        "cum_pct": [r["cum_pct"] for r in rows],
        "rows": rows, "note": "Pareto (urut desc + kumulatif %).",
    }


def compute_box(values: Sequence[Any]) -> Dict[str, Any]:
    """Box plot: kuartil + IQR."""
    arr = _clean(values)
    if len(arr) < 2:
        return {"available": False, "chart_type": "box",
                "reason": "Data belum cukup (min. 2 titik)."}
    a = np.asarray(arr, dtype=float)
    q1, med, q3 = (float(np.percentile(a, 25)),
                   float(np.percentile(a, 50)),
                   float(np.percentile(a, 75)))
    return {
        "available": True, "chart_type": "box",
        "min": _round(float(np.min(a))), "q1": _round(q1),
        "median": _round(med), "q3": _round(q3),
        "max": _round(float(np.max(a))), "iqr": _round(q3 - q1),
        "n": len(arr), "values": [_round(v) for v in arr],
        "note": "Box plot (kuartil + IQR).",
    }


# ======================================================
# RECOMMEND + DISPATCH
# ======================================================
def recommend_chart_type(context: Optional[Dict[str, Any]] = None,
                         values: Optional[Sequence[Any]] = None,
                         phase: str = "control") -> str:
    """
    Heuristik deterministik memilih chart default.
    - Ada kategori (labels) di context     -> pareto
    - Tipe data Y atribut/diskrit           -> run (Measure) / imr (Control)
    - Spec limit tersedia (Measure)         -> histogram
    - Default: control -> imr, measure -> bar/run
    """
    ctx = context or {}
    dtype = str(ctx.get("y_data_type") or ctx.get("data_type") or "").lower()
    has_labels = bool(ctx.get("labels"))
    has_spec = (ctx.get("lsl") is not None and ctx.get("usl") is not None)

    if has_labels:
        return "pareto"
    if phase == "measure":
        if has_spec:
            return "histogram"
        if "attribute" in dtype or "diskrit" in dtype or "discrete" in dtype:
            return "run"
        return "run"
    # control
    if "attribute" in dtype or "diskrit" in dtype or "discrete" in dtype:
        return "run"
    return "imr"


def build_chart(values: Optional[Sequence[Any]] = None,
                chart_type: str = "imr", *,
                lsl: Optional[float] = None, usl: Optional[float] = None,
                target: Optional[float] = None,
                labels: Optional[Sequence[Any]] = None,
                counts: Optional[Sequence[Any]] = None,
                context: Optional[Dict[str, Any]] = None,
                phase: str = "control") -> Dict[str, Any]:
    """Dispatcher: kembalikan dict payload seragam (selalu punya 'chart_type')."""
    ct = (chart_type or "").lower().strip()
    if ct in ("", "auto"):
        ct = recommend_chart_type(context=context, values=values, phase=phase)

    if ct == "imr":
        return compute_imr(values or [])
    if ct == "individuals":
        return compute_individuals(values or [])
    if ct == "run":
        return compute_run(values or [])
    if ct == "histogram":
        return compute_histogram(values or [], lsl=lsl, usl=usl, target=target)
    if ct == "pareto":
        return compute_pareto(labels or [], counts or [])
    if ct == "box":
        return compute_box(values or [])
    # fallback
    return compute_individuals(values or [])


# ======================================================
# RENDER (matplotlib) — dipakai Streamlit & Word
# ======================================================
def build_figure(chart_dict: Dict[str, Any], title: str = "",
                 y_label: str = "Nilai", x_label: str = "Sampel"):
    """Bangun matplotlib Figure dari payload chart. Return Figure atau None."""
    if not chart_dict or not chart_dict.get("available"):
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    ct = chart_dict.get("chart_type", "individuals")

    if ct == "imr":
        i = chart_dict.get("i", {})
        mr = chart_dict.get("mr", {})
        iv, mv = i.get("values", []), mr.get("values", [])
        fig, axes = plt.subplots(2, 1, figsize=(7.5, 5.2), sharex=False)
        ax1, ax2 = axes
        x1 = range(1, len(iv) + 1)
        ax1.plot(x1, iv, marker="o", color="#1f77b4", label="Nilai")
        ax1.axhline(i.get("center"), color="#16a34a", label="Center")
        ax1.axhline(i.get("ucl"), color="#dc2626", ls="--", label="UCL")
        ax1.axhline(i.get("lcl"), color="#dc2626", ls="--", label="LCL")
        ax1.set_title((title or "Control Chart") + " — Individuals (I)", fontsize=10, fontweight="bold")
        ax1.set_ylabel(y_label, fontsize=9); ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=7, loc="best")
        x2 = range(2, len(mv) + 2)
        ax2.plot(x2, mv, marker="o", color="#7c3aed", label="Moving Range")
        ax2.axhline(mr.get("center"), color="#16a34a", label="MR-bar")
        ax2.axhline(mr.get("ucl"), color="#dc2626", ls="--", label="UCL")
        ax2.set_title("Moving Range (MR)", fontsize=10, fontweight="bold")
        ax2.set_xlabel(x_label, fontsize=9); ax2.set_ylabel("MR", fontsize=9)
        ax2.grid(True, alpha=0.3); ax2.legend(fontsize=7, loc="best")
        fig.tight_layout()
        return fig

    if ct in ("individuals", "run"):
        vals = chart_dict.get("values", [])
        fig, ax = plt.subplots(figsize=(7.5, 3.4))
        x = range(1, len(vals) + 1)
        ax.plot(x, vals, marker="o", color="#1f77b4", label="Nilai")
        if ct == "individuals":
            ax.axhline(chart_dict.get("center"), color="#16a34a", label="Center")
            ax.axhline(chart_dict.get("ucl"), color="#dc2626", ls="--", label="UCL")
            ax.axhline(chart_dict.get("lcl"), color="#dc2626", ls="--", label="LCL")
        else:
            ax.axhline(chart_dict.get("median"), color="#16a34a", ls="--", label="Median")
        ax.set_title(title or ("Individuals Chart" if ct == "individuals" else "Run Chart"),
                     fontsize=10, fontweight="bold")
        ax.set_xlabel(x_label, fontsize=9); ax.set_ylabel(y_label, fontsize=9)
        ax.grid(True, alpha=0.3); ax.legend(fontsize=7, loc="best")
        fig.tight_layout()
        return fig

    if ct == "histogram":
        edges = chart_dict.get("bin_edges", [])
        counts = chart_dict.get("counts", [])
        fig, ax = plt.subplots(figsize=(7.5, 3.6))
        if edges and counts:
            widths = [edges[i + 1] - edges[i] for i in range(len(counts))]
            ax.bar(edges[:-1], counts, width=widths, align="edge",
                   color="#3b82f6", edgecolor="white")
        for key, col, lab in (("lsl", "#dc2626", "LSL"),
                              ("usl", "#dc2626", "USL"),
                              ("target", "#16a34a", "Target")):
            v = chart_dict.get(key)
            if v is not None:
                ax.axvline(v, color=col, ls="--", label=lab)
        ax.set_title(title or "Histogram", fontsize=10, fontweight="bold")
        ax.set_xlabel(y_label, fontsize=9); ax.set_ylabel("Frekuensi", fontsize=9)
        ax.grid(True, alpha=0.3)
        cp, cpk = chart_dict.get("cp"), chart_dict.get("cpk")
        if cp is not None or cpk is not None:
            ax.text(0.98, 0.95, f"Cp={cp}  Cpk={cpk}", transform=ax.transAxes,
                    ha="right", va="top", fontsize=8,
                    bbox=dict(boxstyle="round", fc="#fef3c7", ec="#e2e8f0"))
        if any(chart_dict.get(k) is not None for k in ("lsl", "usl", "target")):
            ax.legend(fontsize=7, loc="upper left")
        fig.tight_layout()
        return fig

    if ct == "pareto":
        labels = chart_dict.get("labels", [])
        counts = chart_dict.get("counts", [])
        cum = chart_dict.get("cum_pct", [])
        fig, ax = plt.subplots(figsize=(7.5, 3.8))
        x = range(len(labels))
        ax.bar(x, counts, color="#3b82f6", edgecolor="white")
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel("Jumlah", fontsize=9)
        ax.set_title(title or "Pareto", fontsize=10, fontweight="bold")
        ax2 = ax.twinx()
        ax2.plot(x, cum, marker="o", color="#dc2626", label="Kumulatif %")
        ax2.set_ylim(0, 110); ax2.set_ylabel("Kumulatif %", fontsize=9)
        ax2.axhline(80, color="#16a34a", ls=":", label="80%")
        ax2.legend(fontsize=7, loc="lower right")
        fig.tight_layout()
        return fig

    if ct == "box":
        vals = chart_dict.get("values", [])
        fig, ax = plt.subplots(figsize=(5.5, 3.6))
        ax.boxplot(vals, vert=True, patch_artist=True,
                   boxprops=dict(facecolor="#bfdbfe", color="#1e40af"),
                   medianprops=dict(color="#dc2626"))
        ax.set_title(title or "Box Plot", fontsize=10, fontweight="bold")
        ax.set_ylabel(y_label, fontsize=9); ax.grid(True, alpha=0.3)
        fig.tight_layout()
        return fig

    return None


def chart_to_png_b64(chart_dict: Dict[str, Any], title: str = "",
                     y_label: str = "Nilai") -> Optional[str]:
    """Render chart ke base64 PNG (untuk embed di Word). None bila gagal."""
    fig = build_figure(chart_dict, title=title, y_label=y_label)
    if fig is None:
        return None
    try:
        import io, base64
        import matplotlib.pyplot as plt
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        try:
            import matplotlib.pyplot as plt
            plt.close(fig)
        except Exception:
            pass
        return None


def render_chart_streamlit(chart_dict: Dict[str, Any], title: str = "",
                           y_label: str = "Nilai", key: str = "") -> None:
    """Gambar chart di Streamlit (pakai matplotlib via st.pyplot). Aman dipanggil
    hanya dalam konteks Streamlit."""
    import streamlit as st
    if not chart_dict or not chart_dict.get("available"):
        st.info((chart_dict or {}).get("reason", "Data belum cukup untuk chart."))
        return
    fig = build_figure(chart_dict, title=title, y_label=y_label)
    if fig is None:
        st.info("Chart tidak dapat digambar.")
        return
    st.pyplot(fig)
    # Metrik ringkas per jenis
    ct = chart_dict.get("chart_type")
    if ct in ("imr", "individuals"):
        c1, c2, c3 = st.columns(3)
        c1.metric("UCL", chart_dict.get("ucl"))
        c2.metric("Center", chart_dict.get("center"))
        c3.metric("LCL", chart_dict.get("lcl"))
    elif ct == "histogram":
        c1, c2, c3 = st.columns(3)
        c1.metric("Mean", chart_dict.get("mean"))
        c2.metric("Cp", chart_dict.get("cp"))
        c3.metric("Cpk", chart_dict.get("cpk"))
    elif ct == "box":
        c1, c2, c3 = st.columns(3)
        c1.metric("Q1", chart_dict.get("q1"))
        c2.metric("Median", chart_dict.get("median"))
        c3.metric("Q3", chart_dict.get("q3"))
