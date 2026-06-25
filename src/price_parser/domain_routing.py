from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class DomainPolicy:
    code: str
    preferred_sources: tuple[str, ...]
    forbidden_inferences: tuple[str, ...]


DOMAIN_POLICIES: dict[str, DomainPolicy] = {
    "METAL_PRODUCT": DomainPolicy(
        code="METAL_PRODUCT",
        preferred_sources=("ГОСТ/ТУ, указанные в строке", "реестр подтверждённых марок"),
        forbidden_inferences=(
            "не объявлять эквивалентность марок без источника",
            "не дописывать отсутствующие размеры",
        ),
    ),
    "FERROUS_SCRAP": DomainPolicy(
        code="FERROUS_SCRAP",
        preferred_sources=("ГОСТ 2787-2024",),
        forbidden_inferences=(
            "не определять конкретную марку стали только по группе лома",
            "не дописывать отсутствующие вид, категорию или химический состав",
        ),
    ),
    "NONFERROUS_SCRAP": DomainPolicy(
        code="NONFERROUS_SCRAP",
        preferred_sources=("ГОСТ Р 54564-2025",),
        forbidden_inferences=(
            "не определять марку сплава без явного обозначения или анализа",
            "не смешивать классы цветного лома с группами легированного чёрного лома",
        ),
    ),
    "WELDING_CONSUMABLE": DomainPolicy(
        code="WELDING_CONSUMABLE",
        preferred_sources=("AWS/ISO/ГОСТ на сварочные материалы", "документация производителя"),
        forbidden_inferences=(
            "не подменять классификацию сварочного материала маркой основного металла",
        ),
    ),
}


def classify_domain(description: str, profile: str) -> str:
    text = description.upper().replace("Ё", "Е")
    if profile in {"ЛОМ", "ШИХТА"}:
        if re.search(r"\b(МЕДН|ЛАТУН|БРОНЗ|АЛЮМИН|СВИНЕЦ|ЦИНК|ТИТАН)\w*", text):
            return "NONFERROUS_SCRAP"
        return "FERROUS_SCRAP"
    if (
        profile in {"ЭЛЕКТРОД", "ПРОВОЛОКА"}
        and re.search(r"\b(?:E\d{3}|ER\d{3}|СВ[-\s]?\d)", text)
    ) or re.search(r"СВАРОЧН|ПОРОШКОВАЯ\s+ПРОВ", text):
        return "WELDING_CONSUMABLE"
    return "METAL_PRODUCT"


def policy_payload(domain: str) -> dict[str, object]:
    policy = DOMAIN_POLICIES.get(domain, DOMAIN_POLICIES["METAL_PRODUCT"])
    return {
        "domain": policy.code,
        "preferred_sources": list(policy.preferred_sources),
        "forbidden_inferences": list(policy.forbidden_inferences),
    }
