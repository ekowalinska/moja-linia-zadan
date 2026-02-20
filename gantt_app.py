from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd
import plotly.express as px
import streamlit as st

DATA_FILE = Path("tasks.json")

PRIORITY_COLORS = {
    "Niski": "#7aa6ff",
    "Średni": "#f2c14e",
    "Wysoki": "#ff7a59",
    "Krytyczny": "#e63946",
}
PRIORITY_ORDER = ["Krytyczny", "Wysoki", "Średni", "Niski"]


@dataclass
class Task:
    id: str
    name: str
    start: str            # ISO YYYY-MM-DD
    plan_end: str         # ISO YYYY-MM-DD (deadline)
    priority: str
    notes: str = ""
    done: bool = False
    done_date: str = ""   # ISO YYYY-MM-DD


def load_tasks() -> List[Task]:
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        tasks = []
        for d in data:
            tasks.append(Task(
                id=str(d["id"]),
                name=str(d["name"]),
                start=str(d["start"]),
                plan_end=str(d["plan_end"]),
                priority=str(d["priority"]),
                notes=str(d.get("notes", "")),
                done=bool(d.get("done", False)),
                done_date=str(d.get("done_date", "")),
            ))
        return tasks
    except Exception:
        return []


def save_tasks(tasks: List[Task]) -> None:
    DATA_FILE.write_text(
        json.dumps([asdict(t) for t in tasks], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def iso(d: date) -> str:
    return d.isoformat()


def validate_dates(start: date, plan_end: date) -> Optional[str]:
    if plan_end < start:
        return "Deadline nie może być wcześniejszy niż start."
    return None


def tasks_to_df(tasks: List[Task]) -> pd.DataFrame:
    if not tasks:
        return pd.DataFrame(columns=[
            "id", "Zadanie", "Start", "Deadline", "Priorytet", "Notatki",
            "Zakończone", "Data zakończenia"
        ])

    rows = []
    for t in tasks:
        rows.append({
            "id": t.id,
            "Zadanie": t.name,
            "Start": t.start,
            "Deadline": t.plan_end,
            "Priorytet": t.priority,
            "Notatki": t.notes,
            "Zakończone": t.done,
            "Data zakończenia": t.done_date,
        })

    df = pd.DataFrame(rows)
    # trzymamy jako datetime dla edycji i wykresu
    df["Start"] = pd.to_datetime(df["Start"])
    df["Deadline"] = pd.to_datetime(df["Deadline"])
    df["Data zakończenia"] = pd.to_datetime(df["Data zakończenia"], errors="coerce")
    return df


def df_to_tasks(df: pd.DataFrame, prev_tasks: List[Task]) -> List[Task]:
    """
    Aktualizuje listę Task na podstawie edytowanej tabeli.
    Zachowuje stabilne id.
    Ustala done_date automatycznie przy przejściu done=False -> True.
    Czyści done_date przy przejściu done=True -> False.
    """
    prev_by_id = {t.id: t for t in prev_tasks}
    today = date.today().isoformat()

    new_tasks: List[Task] = []
    for _, row in df.iterrows():
        task_id = str(row["id"])
        prev = prev_by_id.get(task_id)

        name = str(row["Zadanie"]).strip()
        priority = str(row["Priorytet"])
        notes = str(row.get("Notatki", "") or "").strip()

        start_dt = pd.to_datetime(row["Start"]).date()
        deadline_dt = pd.to_datetime(row["Deadline"]).date()

        done_now = bool(row["Zakończone"])
        done_date_raw = row.get("Data zakończenia", pd.NaT)

        # daty: start/deadline do ISO
        start_iso = start_dt.isoformat()
        deadline_iso = deadline_dt.isoformat()

        # done_date: auto logika
        prev_done = prev.done if prev else False
        prev_done_date = prev.done_date if prev else ""

        if done_now and not prev_done:
            # właśnie zaznaczone jako zakończone
            done_date_iso = today
        elif (not done_now) and prev_done:
            # właśnie odznaczone
            done_date_iso = ""
        else:
            # bez zmiany stanu - bierz z tabeli jeśli istnieje, inaczej zachowaj poprzednią
            if pd.isna(done_date_raw):
                done_date_iso = prev_done_date if prev else ""
            else:
                done_date_iso = pd.to_datetime(done_date_raw).date().isoformat()

        new_tasks.append(Task(
            id=task_id,
            name=name,
            start=start_iso,
            plan_end=deadline_iso,
            priority=priority,
            notes=notes,
            done=done_now,
            done_date=done_date_iso,
        ))

    return new_tasks


def make_gantt(df: pd.DataFrame, show_done: bool):
    if df.empty:
        st.info("Dodaj pierwsze zadanie po lewej — wykres pojawi się tutaj.")
        return

    df = df.copy()

    if not show_done:
        df = df[df["Zakończone"] == False]

    if df.empty:
        st.info("Brak zadań do pokazania przy aktualnym filtrze.")
        return

    # koniec na wykresie: aktywne -> Deadline, zakończone -> Data zakończenia (lub Deadline)
    df["Koniec (wykres)"] = df["Deadline"]
    mask_done = df["Zakończone"] == True
    df.loc[mask_done, "Koniec (wykres)"] = df.loc[mask_done, "Data zakończenia"].fillna(df.loc[mask_done, "Deadline"])

    # sekcje na osi Y: aktywne u góry, zakończone na dole
    df["Sekcja"] = df["Zakończone"].apply(lambda x: "✅ ZAKOŃCZONE" if x else "🟦 AKTYWNE")
    df["Sekcja_sort"] = pd.Categorical(df["Sekcja"], categories=["🟦 AKTYWNE", "✅ ZAKOŃCZONE"], ordered=True)
    df["Priorytet_sort"] = pd.Categorical(df["Priorytet"], categories=PRIORITY_ORDER, ordered=True)
    df = df.sort_values(["Sekcja_sort", "Priorytet_sort", "Start"], ascending=[True, True, True])

    df["Y"] = df["Sekcja"] + "  |  " + df["Zadanie"]

    fig = px.timeline(
        df,
        x_start="Start",
        x_end="Koniec (wykres)",
        y="Y",
        color="Priorytet",
        color_discrete_map=PRIORITY_COLORS,
        hover_data={
            "Zadanie": True,
            "Start": True,
            "Deadline": True,
            "Zakończone": True,
            "Data zakończenia": True,
            "Notatki": True,
        },
    )

    fig.update_yaxes(categoryorder="array", categoryarray=df["Y"].tolist(), autorange="reversed")
    fig.update_layout(
        height=max(460, 80 + 32 * len(df)),
        margin=dict(l=10, r=10, t=30, b=10),
        legend_title_text="Priorytet",
        xaxis_title="Czas",
        yaxis_title="",
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------- UI ----------------

st.set_page_config(page_title="Moja linia zadań (Gantt)", layout="wide")
st.title("Moja linia zadań — wykres Gantta")

if "tasks" not in st.session_state:
    st.session_state.tasks = load_tasks()

left, right = st.columns([1, 2], gap="large")

with left:
    st.subheader("Dodaj zadanie")

    with st.form("add_task", clear_on_submit=True):
        name = st.text_input("Nazwa zadania", placeholder="np. Budstol – doprecyzować warunki klimatyzacji")
        priority = st.selectbox("Priorytet", PRIORITY_ORDER, index=1)

        deadline = st.date_input("Deadline (planowany koniec)", value=date.today())

        deadline_only = st.checkbox("Ustaw start automatycznie na dziś (dodaję tylko deadline)", value=True)
        if deadline_only:
            start = date.today()
            st.caption(f"Start ustawiony na: {start.isoformat()}")
        else:
            start = st.date_input("Start", value=date.today())

        notes = st.text_area("Notatki (opcjonalnie)", height=90, placeholder="np. zależne od odpowiedzi prawnika")

        submitted = st.form_submit_button("➕ Dodaj")
        if submitted:
            if not name.strip():
                st.error("Podaj nazwę zadania.")
            else:
                err = validate_dates(start, deadline)
                if err:
                    st.error(err)
                else:
                    new_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
                    st.session_state.tasks.append(Task(
                        id=new_id,
                        name=name.strip(),
                        start=iso(start),
                        plan_end=iso(deadline),
                        priority=priority,
                        notes=notes.strip(),
                        done=False,
                        done_date="",
                    ))
                    save_tasks(st.session_state.tasks)
                    st.success("Dodano.")
                    st.rerun()

    st.divider()
    st.subheader("Edytuj / zakończ zadania (klik w tabeli)")

    df = tasks_to_df(st.session_state.tasks)

    if df.empty:
        st.caption("Brak zadań.")
    else:
        # Filtr priorytetów w tabeli (opcjonalnie)
        pri_filter = st.multiselect("Filtr priorytetów w tabeli", PRIORITY_ORDER, default=PRIORITY_ORDER)
        df_view = df[df["Priorytet"].isin(pri_filter)].copy()

        edited = st.data_editor(
            df_view,
            hide_index=True,
            use_container_width=True,
            column_config={
                "id": st.column_config.TextColumn("id", disabled=True),
                "Zadanie": st.column_config.TextColumn("Zadanie"),
                "Start": st.column_config.DateColumn("Start"),
                "Deadline": st.column_config.DateColumn("Deadline"),
                "Priorytet": st.column_config.SelectboxColumn("Priorytet", options=PRIORITY_ORDER),
                "Zakończone": st.column_config.CheckboxColumn("Zakończone"),
                "Data zakończenia": st.column_config.DateColumn("Data zakończenia"),
                "Notatki": st.column_config.TextColumn("Notatki"),
            },
            disabled=["id"],  # id nieedytowalne
            key="editor",
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("💾 Zapisz zmiany"):
                # scal zmiany: podmień tylko te wiersze, które były widoczne w df_view
                df_full = df.copy()
                edited_ids = set(edited["id"].astype(str).tolist())

                # aktualizuj w pełnym df rekordy o tych id
                for _, r in edited.iterrows():
                    rid = str(r["id"])
                    idx = df_full.index[df_full["id"].astype(str) == rid]
                    if len(idx) == 1:
                        df_full.loc[idx[0], :] = r

                # walidacja dat
                bad = df_full[df_full["Deadline"] < df_full["Start"]]
                if not bad.empty:
                    st.error("Masz co najmniej jedno zadanie, gdzie Deadline < Start. Popraw i zapisz ponownie.")
                else:
                    st.session_state.tasks = df_to_tasks(df_full, st.session_state.tasks)
                    save_tasks(st.session_state.tasks)
                    st.success("Zapisano.")
                    st.rerun()

        with col_b:
            if st.button("🧹 Wyczyść wszystko"):
                st.session_state.tasks = []
                save_tasks(st.session_state.tasks)
                st.warning("Wyczyszczono.")
                st.rerun()


with right:
    st.subheader("Oś czasu (Gantt)")

    df_plot = tasks_to_df(st.session_state.tasks)

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        show_done = st.checkbox("Pokaż zakończone", value=True)
    with c2:
        pri_plot = st.multiselect("Priorytety", options=PRIORITY_ORDER, default=PRIORITY_ORDER)
    with c3:
        text_filter = st.text_input("Filtruj po nazwie", placeholder="np. Budstol / IT / badanie...")

    if not df_plot.empty:
        df_plot = df_plot[df_plot["Priorytet"].isin(pri_plot)]
        if text_filter.strip():
            df_plot = df_plot[df_plot["Zadanie"].str.contains(text_filter.strip(), case=False, na=False)]

    make_gantt(df_plot, show_done=show_done)
