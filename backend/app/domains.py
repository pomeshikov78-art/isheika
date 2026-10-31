"""Предметные области заказчика и подсказки под них.

Система общая, но качество поиска и анализа заметно растёт, когда план
строится с учётом отраслевой терминологии. Крупная телекоммуникационная
компания спрашивает не «про технологии вообще», а про конкретные классы
решений, и у каждого свои профильные термины, свои базы и свои критерии
оценки.

Профиль области решает три задачи:
  * подсказывает планировщику английские термины - без них выборка
    в международных базах беднее втрое;
  * задаёт критерии сравнения, характерные для класса решений;
  * даёт готовые формулировки для интерфейса, чтобы аналитик начинал
    не с чистого листа.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Domain:
    key: str
    title: str
    keywords: list[str] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


DOMAINS: list[Domain] = [
    Domain(
        key="telecom",
        title="Телекоммуникационные технологии",
        keywords=["telecommunications", "5G", "6G", "Open RAN", "network slicing",
                  "optical transport", "DWDM", "backhaul", "spectrum efficiency"],
        criteria=["Пропускная способность", "Задержка", "Энергоэффективность на бит",
                  "Стоимость владения", "Зрелость стандарта"],
        examples=[
            "Оцени перспективы Open RAN для сетей мобильной связи до 2030 года",
            "Сравни технологии транспортных сетей DWDM и когерентной оптики 800G",
        ],
    ),
    Domain(
        key="it",
        title="Информационные технологии",
        keywords=["information technology", "cloud native", "kubernetes",
                  "observability", "data platform", "microservices"],
        criteria=["Зрелость экосистемы", "Стоимость владения", "Совместимость",
                  "Требования к персоналу", "Риск вендорной зависимости"],
        examples=[
            "Оцени зрелость платформ наблюдаемости для распределённых систем",
        ],
    ),
    Domain(
        key="automation",
        title="Средства автоматизации",
        keywords=["industrial automation", "SCADA", "PLC", "RPA",
                  "process orchestration", "network automation"],
        criteria=["Надёжность", "Совместимость с оборудованием", "Стоимость внедрения",
                  "Требования к квалификации", "Сроки окупаемости"],
        examples=[
            "Выяви риски внедрения автоматизации эксплуатации сети оператора связи",
        ],
    ),
    Domain(
        key="comms",
        title="Средства связи различного вида",
        keywords=["radio communication", "satellite communication", "LEO constellation",
                  "microwave link", "trunking", "private LTE"],
        criteria=["Дальность", "Помехоустойчивость", "Пропускная способность",
                  "Стоимость канала", "Регуляторные ограничения"],
        examples=[
            "Спрогнозируй развитие спутниковой связи на низких орбитах до 2035 года",
        ],
    ),
    Domain(
        key="ai",
        title="Технологии искусственного интеллекта",
        keywords=["artificial intelligence", "large language model", "edge AI",
                  "AI accelerator", "inference optimization", "AIOps"],
        criteria=["Качество на профильных задачах", "Стоимость вывода",
                  "Требования к оборудованию", "Зрелость инструментов", "Регуляторные риски"],
        examples=[
            "Оцени применимость AIOps для управления сетью оператора связи",
        ],
    ),
    Domain(
        key="iot",
        title="Технологии интернета вещей",
        keywords=["internet of things", "NB-IoT", "LoRaWAN", "LPWAN",
                  "edge computing", "device management", "massive MTC"],
        criteria=["Энергопотребление устройства", "Дальность", "Стоимость модуля",
                  "Плотность подключений", "Безопасность"],
        examples=[
            "Сравни NB-IoT и LoRaWAN для телеметрии распределённой инфраструктуры",
        ],
    ),
    Domain(
        key="digital_twin",
        title="Технологии цифровых двойников",
        keywords=["digital twin", "network digital twin", "simulation",
                  "model based systems engineering", "predictive maintenance"],
        criteria=["Точность модели", "Требования к данным", "Вычислительная стоимость",
                  "Интеграция с эксплуатацией", "Зрелость инструментов"],
        examples=[
            "Оцени зрелость цифровых двойников сети связи и барьеры их внедрения",
        ],
    ),
    Domain(
        key="power",
        title="Энергообеспечение: аккумуляторы, зарядные устройства, ИБП",
        keywords=["battery energy storage", "lithium iron phosphate", "UPS",
                  "rectifier", "diesel generator", "power backup", "DC power plant"],
        criteria=["Удельная энергия", "Ресурс циклов", "Диапазон температур",
                  "Стоимость киловатт-часа", "Время автономной работы"],
        examples=[
            "Оцени перспективы литий-железо-фосфатных аккумуляторов для базовых станций",
            "Сравни источники бесперебойного питания для узлов связи по стоимости владения",
        ],
    ),
]

BY_KEY = {domain.key: domain for domain in DOMAINS}


def detect(query: str) -> Domain | None:
    """Определяет область по запросу.

    Сопоставление грубое, по вхождению профильных слов: точность здесь
    не критична, а ошибка дешева - подсказка лишь дополняет промпт,
    но не подменяет собой запрос пользователя.
    """
    text = (query or "").lower()
    best, best_score = None, 0

    for domain in DOMAINS:
        # Длинная подсказка специфичнее короткой: «цифровых двойн» говорит
        # об области больше, чем «сет», которое есть в половине запросов
        score = sum(_weight(word) for word in domain.keywords if word.lower() in text)
        score += sum(_weight(word) for word in _russian_hints(domain) if word in text)
        if score > best_score:
            best, best_score = domain, score

    return best if best_score > 0 else None


def _weight(word: str) -> int:
    return 2 if len(word) >= 8 else 1


_RU_HINTS = {
    "telecom": ["телеком", "связ", "сет", "оператор", "радиодоступ", "транспортн"],
    "it": ["информацион", "облач", "микросервис", "платформ данных"],
    "automation": ["автоматизац", "скада", "плк", "роботизац"],
    "comms": ["радиосвяз", "спутник", "релейн", "транкинг"],
    "ai": ["искусственн", "нейросет", "машинн обучен", "языков", "нейронн", "aiops", "llm"],
    "iot": ["интернет вещей", "телеметри", "датчик", "устройств"],
    # Основы, а не словоформы: «цифров двойник» не совпадало
    # с «цифровых двойников» из-за окончаний
    "digital_twin": ["двойник", "имитацион", "предиктивн", "цифровых двойн"],
    "power": ["аккумулятор", "зарядн", "электроагрегат", "бесперебойн", "питани", "энергообеспеч"],
}


def _russian_hints(domain: Domain) -> list[str]:
    return _RU_HINTS.get(domain.key, [])


def prompt_hint(domain: Domain | None) -> str:
    """Отраслевая подсказка для планировщика."""
    if domain is None:
        return ""
    return (
        f"Предметная область: {domain.title}. "
        f"Профильные термины для англоязычного поиска: {', '.join(domain.keywords[:8])}. "
        f"Критерии сравнения, принятые в этой области: {', '.join(domain.criteria)}."
    )
