"""FastAPI backend for the ASX Portfolio Analyser (v0.4)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from pipeline import storage
from portfolio.profile import GeoTilt, RiskProfile, UserProfile, THEME_TICKERS
from portfolio.construct import construct
from ai import client as ai_client
from ai import parse_profile as ai_parse
from ai import explain as ai_explain

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent
STATIC_DIR = WEB_ROOT / "static"

app = FastAPI(
    title="ASX Portfolio Analyser",
    description="Educational analysis tool for ASX equities and ETFs. Not financial advice.",
    version="0.4.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ProfileRequest(BaseModel):
    capital: float = Field(..., gt=0, le=10_000_000)
    risk_profile: str
    horizon_years: int = Field(..., ge=0, le=80)
    prefer_income: bool = False
    esg_only: bool = False
    etfs_only: bool = False
    exclude_sectors: list[str] = Field(default_factory=list)
    include_only_sectors: list[str] = Field(default_factory=list)
    exclude_tickers: list[str] = Field(default_factory=list)
    preferred_themes: list[str] = Field(default_factory=list)
    geo_tilt: str = "neutral"
    prefer_hedged: bool = False
    min_dividend_yield: float = Field(0.0, ge=0, le=0.20)
    max_volatility: Optional[float] = Field(None, gt=0, le=2.0)
    min_history_years: int = Field(3, ge=1, le=10)
    max_holdings: int = Field(8, ge=3, le=30)
    max_position_size: float = Field(0.15, gt=0, le=1.0)

    @field_validator("risk_profile")
    @classmethod
    def _validate_risk(cls, v: str) -> str:
        valid = {r.value for r in RiskProfile}
        if v not in valid:
            raise ValueError(f"risk_profile must be one of {sorted(valid)}")
        return v

    @field_validator("geo_tilt")
    @classmethod
    def _validate_geo(cls, v: str) -> str:
        valid = {g.value for g in GeoTilt}
        if v not in valid:
            raise ValueError(f"geo_tilt must be one of {sorted(valid)}")
        return v


class HoldingResponse(BaseModel):
    ticker: str
    name: str
    asset_class: str
    weight: float
    dollars: float
    sharpe_used: Optional[float]
    rationale: str
    return_1y: Optional[float]
    return_3y: Optional[float]
    return_5y: Optional[float]
    volatility_1y: Optional[float]
    max_drawdown_5y: Optional[float]
    dividend_yield_ttm: Optional[float]


class ProjectionResponse(BaseModel):
    horizon_years: int
    median: float
    low: float
    high: float
    median_return_pct: float


class PortfolioResponse(BaseModel):
    holdings: list[HoldingResponse]
    target_allocation: dict[str, float]
    realised_allocation: dict[str, float]
    expected_return: Optional[float]
    expected_volatility: Optional[float]
    expected_max_drawdown: Optional[float]
    expected_dividend_yield: Optional[float]
    capital: float
    notes: list[str]
    projection: Optional[ProjectionResponse]


class ParseRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=2000)


class ParseResponse(BaseModel):
    fields: dict[str, Any]
    ai_available: bool


class ExplainRequest(BaseModel):
    profile: dict[str, Any]
    result: dict[str, Any]


class ExplainResponse(BaseModel):
    text: Optional[str]
    ai_available: bool


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "ai_available": ai_client.is_available()}


@app.get("/api/sectors")
def sectors() -> dict:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT sector FROM instruments WHERE type='stock' AND sector IS NOT NULL ORDER BY sector"
        ).fetchall()
    return {"sectors": [r[0] for r in rows]}


@app.get("/api/themes")
def themes() -> dict:
    return {"themes": list(THEME_TICKERS.keys())}


@app.post("/api/portfolio", response_model=PortfolioResponse)
def portfolio(req: ProfileRequest) -> PortfolioResponse:
    try:
        profile = UserProfile(
            capital=req.capital,
            risk_profile=RiskProfile(req.risk_profile),
            horizon_years=req.horizon_years,
            prefer_income=req.prefer_income,
            esg_only=req.esg_only,
            etfs_only=req.etfs_only,
            exclude_sectors=tuple(req.exclude_sectors),
            include_only_sectors=tuple(req.include_only_sectors),
            exclude_tickers=tuple(req.exclude_tickers),
            preferred_themes=tuple(req.preferred_themes),
            geo_tilt=GeoTilt(req.geo_tilt),
            prefer_hedged=req.prefer_hedged,
            min_dividend_yield=req.min_dividend_yield,
            max_volatility=req.max_volatility,
            min_history_years=req.min_history_years,
            max_holdings=req.max_holdings,
            max_position_size=req.max_position_size,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        result = construct(profile)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return PortfolioResponse(
        holdings=[HoldingResponse(**h.__dict__) for h in result.holdings],
        target_allocation=result.target_allocation,
        realised_allocation=result.realised_allocation,
        expected_return=result.expected_return,
        expected_volatility=result.expected_volatility,
        expected_max_drawdown=result.expected_max_drawdown,
        expected_dividend_yield=result.expected_dividend_yield,
        capital=result.capital,
        notes=result.notes,
        projection=(ProjectionResponse(**result.projection.__dict__)
                    if result.projection else None),
    )


@app.post("/api/parse", response_model=ParseResponse)
def parse(req: ParseRequest) -> ParseResponse:
    if not ai_client.is_available():
        return ParseResponse(fields={}, ai_available=False)
    fields = ai_parse.parse(req.description) or {}
    return ParseResponse(fields=fields, ai_available=True)


@app.post("/api/explain", response_model=ExplainResponse)
def explain(req: ExplainRequest) -> ExplainResponse:
    if not ai_client.is_available():
        return ExplainResponse(text=None, ai_available=False)
    text = ai_explain.explain(req.profile, req.result)
    return ExplainResponse(text=text, ai_available=True)
