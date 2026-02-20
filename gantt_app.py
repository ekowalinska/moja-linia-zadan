from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

import pandas as pd
import plotly.express as px
import streamlit as st

import gspread
from google.oauth2.service_account import Credentials


PRIORITY_COLORS = {
    "Niski": "#7aa6ff",
    "Średni": "#f2c14e",
    "Wysoki": "#ff7a59",
    "Krytyczny": "#e63946",
}
PRIORITY_ORDER = ["Krytyczny", "Wysoki", "Średni", "Niski"]

SHEET_HEADERS = ["id", "name", "start", "plan_end", "priority", "notes", "done", "done_date"]


@dataclass
class Task:
    id: str
    name: str
    start: str            # ISO YYYY-MM-DD
    plan_end: str         # ISO YYYY-MM-DD
    priority: str
    notes: str = ""
    done: bool = False
    done_date: str = ""   # ISO YYYY-MM-DD


@st.cache_resource
def get_gspread_client():
    sa = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(
        dict(sa),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


def get_worksheet():
    sheet_id = st.secrets.get("SHEET_ID", "")
    if not sheet_id:
        raise RuntimeError("Brakuje SHEET_ID w Streamlit Secrets (Settings → Secrets).")
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    return sh.sheet1  # pierwsza zakładka w pliku


def ensure_header(ws):
    values = ws.get_all_values()
    if not values:
        ws.append_row(SHEET_HEADERS)
        return
    if values[0] != SHEET_HEADERS:
        ws.update("A1", [SHEET_HEADERS])


def load_tasks() -> List[Task]:
    ws = get_worksheet()
    ensure_header(ws)
    rows = ws.get_all_records()
    tasks: List[Task] = []

    for r in rows:
        done_val = r.get("done", False)
        if isinstance(done_val, str):
            done_val = done_val.strip().lower() in ["true", "1", "tak", "yes", "y"]

        t = Task(
            id=str(r.get("id", "")).strip(),
            name=str(r.get("name", "")).strip(),
            start=str(r.get("start", "")).strip(),
            plan_end=str(r.get("plan_end", "")).strip(),
            priority=str(r.get("priority", "")).strip() or "Średni",
            notes=str(r.get("notes", "") or "").strip(),
            done=bool(done_val),
            done_date=str(r.get("done_date", "") or "").strip(),
        )
        if t.id:
            tasks.append(t)

    return tasks


def save_tasks(tasks: List[Task]) -> None:
    ws = get_worksheet()
    ensure_header(ws)

    ws.batch_clear(["A2:Z"])
    if not tasks:
        return

    rows = []
    for t in tasks:
        rows.append([
            t.id,
            t.name,
            t.start,
            t.plan_end,
            t.priority,
            t.notes,
            "TRUE" if t.done else "FALSE",
            t.done_date,
        ])

    ws.update("A2", rows)


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
    df["Start"] = pd.to_datetime(df["Start"])
    df["Deadline"] = pd.to_datetime(df["Deadline"])
    df["Data zakończenia"] = pd.to_datetime(df["Data zakończenia"], errors="coerce")
    return df


def df_to_tasks(df: pd.DataFrame, prev_tasks: List[Task]) -> List[Task]:
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

        start_iso = start_dt.isoformat()
        deadline_iso = deadline_dt.isoformat()

        prev_done = prev.done if prev else False
        prev_done_date = prev.done_date if prev else ""

        if done_now and not prev_done:
            done_date_iso = today
        elif (not done_now) and prev_done:
            done_date_iso = ""
        else:
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

    df["Koniec (wykres)"] = df["Deadline"]
    mask_done = df["Zakończone"] == True
    df.loc[mask_done, "Koniec (wykres)"] = df.loc[mask_done, "Data zakończenia"].fillna(df.loc[mask_done, "Deadline"])

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


st.set_page_config(page_title="Moja linia zadań (Gantt)", layout="wide")
st.title("Moja linia zadań — wykres Gantta (Google Sheets)")

if "tasks" not in st.session_state:
    try:
        st.session_state.tasks = load_tasks()
    except Exception as e:
        st.error(f"Nie mogę połączyć się z Google Sheets: {e}")
        st.stop()

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

        notes = st.text_area("Notatki (opcjonalnie)", height=90)

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
                    st.success("Dodano i zapisano do Google Sheets.")
                    st.rerun()

    st.divider()
    st.subheader("Edytuj / zakończ (klik w tabeli)")

    df = tasks_to_df(st.session_state.tasks)

    if df.empty:
        st.caption("Brak zadań.")
    else:
        edited = st.data_editor(
            df,
            hide_index=True,
            use_container_width=True,
            disabled=["id"],
            column_config={
                "id": st.column_config.TextColumn("id", disabled=True),
                "Priorytet": st.column_config.SelectboxColumn("Priorytet", options=PRIORITY_ORDER),
                "Zakończone": st.column_config.CheckboxColumn("Zakończone"),
            },
            key="editor",
        )

        if st.button("💾 Zapisz zmiany"):
            bad = edited[edited["Deadline"] < edited["Start"]]
            if not bad.empty:
                st.error("Masz co najmniej jedno zadanie, gdzie Deadline < Start.")
            else:
                st.session_state.tasks = df_to_tasks(edited, st.session_state.tasks)
                save_tasks(st.session_state.tasks)
                st.success("Zapisano do Google Sheets.")
                st.rerun()

with right:
    st.subheader("Oś czasu (Gantt)")

    df_plot = tasks_to_df(st.session_state.tasks)

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        show_done = st.checkbox("Pokaż zakończone", value=True)
    with c2:
        pri_plot = st.multiselect("Priorytety", PRIORITY_ORDER, default=PRIORITY_ORDER)
    with c3:
        text_filter = st.text_input("Filtruj po nazwie", placeholder="np. Budstol / IT / badanie...")

    if not df_plot.empty:
        df_plot = df_plot[df_plot["Priorytet"].isin(pri_plot)]
        if text_filter.strip():
            df_plot = df_plot[df_plot["Zadanie"].str.contains(text_filter.strip(), case=False, na=False)]

    make_gantt(df_plot, show_done=show_done)
