"""Eigenständige, wiederverwendbare Filter-Sets (Datumsbereich + Endungen + Ausschluss-Keywords)
verwalten. Jobs wählen ein bestehendes FilterSet aus, statt Filter pro Job neu zu definieren."""
from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models import FilterSet, FilterSetExtension, FilterSetKeyword, Job
from app.schemas import parse_date_de, parse_extensions, parse_keywords
from app.web.templating import templates

router = APIRouter(prefix="/filters")


async def _load_filter_set_detail(session: AsyncSession, filter_set: FilterSet) -> dict:
    ext_result = await session.execute(
        select(FilterSetExtension).where(FilterSetExtension.filter_set_id == filter_set.id)
    )
    kw_result = await session.execute(
        select(FilterSetKeyword).where(FilterSetKeyword.filter_set_id == filter_set.id)
    )
    return {
        "filter_set": filter_set,
        "extensions": ext_result.scalars().all(),
        "keywords": kw_result.scalars().all(),
    }


@router.get("")
async def list_filter_sets(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(
        select(FilterSet)
        .options(selectinload(FilterSet.extensions), selectinload(FilterSet.keywords))
        .order_by(FilterSet.name)
    )
    filter_sets = result.scalars().all()

    usage_result = await session.execute(select(Job.filter_set_id, func.count(Job.id)).group_by(Job.filter_set_id))
    usage_counts = dict(usage_result.all())

    return templates.TemplateResponse(
        request,
        "filters/list.html",
        {"active_nav": "filters", "filter_sets": filter_sets, "usage_counts": usage_counts},
    )


@router.get("/new")
async def new_filter_set_form(request: Request):
    return templates.TemplateResponse(request, "filters/form.html", {"active_nav": "filters", "detail": None})


@router.get("/{filter_set_id}/edit")
async def edit_filter_set_form(
    request: Request, filter_set_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
):
    filter_set = await session.get(FilterSet, filter_set_id)
    detail = await _load_filter_set_detail(session, filter_set)
    return templates.TemplateResponse(request, "filters/form.html", {"active_nav": "filters", "detail": detail})


async def _save_filter_set_children(
    session: AsyncSession, filter_set: FilterSet, extensions_raw: str, keywords_raw: str
) -> None:
    await session.execute(FilterSetExtension.__table__.delete().where(FilterSetExtension.filter_set_id == filter_set.id))
    for ext in parse_extensions(extensions_raw):
        session.add(FilterSetExtension(filter_set_id=filter_set.id, extension=ext))

    await session.execute(FilterSetKeyword.__table__.delete().where(FilterSetKeyword.filter_set_id == filter_set.id))
    for normalized, display in parse_keywords(keywords_raw):
        session.add(FilterSetKeyword(filter_set_id=filter_set.id, keyword_normalized=normalized, keyword_display=display))


@router.post("")
async def create_filter_set(
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    date_from: Annotated[str, Form()] = "",
    date_to: Annotated[str, Form()] = "",
    extensions: Annotated[str, Form()] = ".pdf",
    keywords: Annotated[str, Form()] = "",
):
    filter_set = FilterSet(name=name.strip(), date_from=parse_date_de(date_from), date_to=parse_date_de(date_to))
    session.add(filter_set)
    await session.flush()
    await _save_filter_set_children(session, filter_set, extensions, keywords)
    await session.commit()
    return RedirectResponse(f"/filters?msg=Filter+{quote(name)}+angelegt", status_code=303)


@router.post("/{filter_set_id}")
async def update_filter_set(
    filter_set_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    date_from: Annotated[str, Form()] = "",
    date_to: Annotated[str, Form()] = "",
    extensions: Annotated[str, Form()] = ".pdf",
    keywords: Annotated[str, Form()] = "",
):
    filter_set = await session.get(FilterSet, filter_set_id)
    filter_set.name = name.strip()
    filter_set.date_from = parse_date_de(date_from)
    filter_set.date_to = parse_date_de(date_to)
    await _save_filter_set_children(session, filter_set, extensions, keywords)
    await session.commit()
    return RedirectResponse(f"/filters?msg=Filter+{quote(name)}+aktualisiert", status_code=303)


@router.post("/{filter_set_id}/delete")
async def delete_filter_set(filter_set_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    filter_set = await session.get(FilterSet, filter_set_id)
    if filter_set is None:
        return RedirectResponse("/filters", status_code=303)
    try:
        await session.delete(filter_set)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return RedirectResponse(
            "/filters?err=Filter+wird+noch+von+mindestens+einem+Job+verwendet+und+kann+nicht+gel%C3%B6scht+werden",
            status_code=303,
        )
    return RedirectResponse("/filters?msg=Filter+gel%C3%B6scht", status_code=303)
