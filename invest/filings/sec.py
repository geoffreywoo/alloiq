from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from ..models import Filing, Holding
from ..util import SEC_USER_AGENT, decimal_or_zero, parse_date


SEC_DATA = "https://data.sec.gov/submissions"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
INFO_NS = {"n": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


def fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_recent_filings(manager: dict[str, Any], forms: set[str] | None = None) -> list[Filing]:
    forms = forms or {"13F-HR", "13F-HR/A", "SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}
    cik = str(manager["cik"]).zfill(10)
    data = fetch_json(f"{SEC_DATA}/CIK{cik}.json")
    recent = data["filings"]["recent"]
    rows: list[Filing] = []
    for acc, form, filing_date, report_date in zip(
        recent["accessionNumber"],
        recent["form"],
        recent["filingDate"],
        recent["reportDate"],
        strict=False,
    ):
        if form not in forms:
            continue
        filing_dt = parse_date(filing_date)
        report_dt = parse_date(report_date)
        if not filing_dt:
            continue
        acc_no_dash = acc.replace("-", "")
        url = f"{SEC_ARCHIVES}/{int(cik)}/{acc_no_dash}/{acc}-index.html"
        rows.append(
            Filing(
                manager_key=manager["key"],
                manager_name=manager.get("display_name") or manager.get("name") or manager["key"],
                cik=cik,
                accession_number=acc,
                form=form,
                filing_date=filing_dt,
                report_date=report_dt,
                url=url,
                raw={"source": "sec_submissions"},
            )
        )
    return rows


def fetch_13f_holdings(
    cik: str,
    accession_number: str,
    symbol_map: dict[str, str],
    bucket_map: dict[str, str],
    issuer_map: dict[str, str] | None = None,
) -> list[Holding]:
    cik_int = str(int(cik))
    acc_no_dash = accession_number.replace("-", "")
    index_url = f"{SEC_ARCHIVES}/{cik_int}/{acc_no_dash}/index.json"
    index = fetch_json(index_url)
    info_name = find_info_table_filename(index)
    if not info_name:
        return []
    value_multiplier = infer_value_multiplier(index, cik_int, acc_no_dash)
    xml_url = f"{SEC_ARCHIVES}/{cik_int}/{acc_no_dash}/{info_name}"
    root = ET.fromstring(fetch_bytes(xml_url))
    holdings: list[Holding] = []
    for row in root.findall("n:infoTable", INFO_NS):
        issuer = row.findtext("n:nameOfIssuer", default="", namespaces=INFO_NS).strip()
        title_class = row.findtext("n:titleOfClass", default="", namespaces=INFO_NS).strip()
        cusip = row.findtext("n:cusip", default="", namespaces=INFO_NS).strip()
        value_usd = decimal_or_zero(row.findtext("n:value", default="0", namespaces=INFO_NS)) * value_multiplier
        shares = decimal_or_zero(row.findtext("n:shrsOrPrnAmt/n:sshPrnamt", default="0", namespaces=INFO_NS))
        put_call = row.findtext("n:putCall", default="", namespaces=INFO_NS).strip()
        symbol = resolve_holding_symbol(issuer, title_class, cusip, symbol_map, issuer_map or DEFAULT_ISSUER_SYMBOL_MAP)
        bucket = bucket_map.get(symbol.upper(), "") if symbol else ""
        holdings.append(
            Holding(
                accession_number=accession_number,
                issuer=issuer,
                title_class=title_class,
                cusip=cusip,
                value_usd=value_usd,
                shares=shares,
                put_call=put_call,
                symbol=symbol,
                bucket=bucket,
                raw={"xml_url": xml_url, "value_units": "usd"},
            )
        )
    return holdings


def parse_13f_xml(xml: bytes | str, accession_number: str = "fixture") -> list[Holding]:
    root = ET.fromstring(xml)
    holdings: list[Holding] = []
    for row in root.findall("n:infoTable", INFO_NS):
        holdings.append(
            Holding(
                accession_number=accession_number,
                issuer=row.findtext("n:nameOfIssuer", default="", namespaces=INFO_NS).strip(),
                title_class=row.findtext("n:titleOfClass", default="", namespaces=INFO_NS).strip(),
                cusip=row.findtext("n:cusip", default="", namespaces=INFO_NS).strip(),
                value_usd=decimal_or_zero(row.findtext("n:value", default="0", namespaces=INFO_NS)),
                shares=decimal_or_zero(row.findtext("n:shrsOrPrnAmt/n:sshPrnamt", default="0", namespaces=INFO_NS)),
                put_call=row.findtext("n:putCall", default="", namespaces=INFO_NS).strip(),
                raw={"value_units": "usd"},
            )
        )
    return holdings


def find_info_table_filename(index: dict[str, Any]) -> str:
    for item in index.get("directory", {}).get("item", []):
        name = item.get("name", "")
        lower = name.lower()
        if lower.endswith(".xml") and "primary_doc" not in lower:
            return name
    return ""


def find_primary_doc_filename(index: dict[str, Any]) -> str:
    for item in index.get("directory", {}).get("item", []):
        name = item.get("name", "")
        lower = name.lower()
        if lower.endswith(".xml") and "primary_doc" in lower:
            return name
    return "primary_doc.xml"


def infer_value_multiplier(index: dict[str, Any], cik_int: str, acc_no_dash: str) -> Any:
    primary_name = find_primary_doc_filename(index)
    try:
        primary_xml = fetch_bytes(f"{SEC_ARCHIVES}/{cik_int}/{acc_no_dash}/{primary_name}")
        primary_total = parse_table_value_total(primary_xml)
    except Exception:
        return 1
    if not primary_total:
        return 1
    info_name = find_info_table_filename(index)
    if not info_name:
        return 1
    try:
        info_xml = fetch_bytes(f"{SEC_ARCHIVES}/{cik_int}/{acc_no_dash}/{info_name}")
        raw_total = sum_info_table_values(info_xml)
    except Exception:
        return 1
    if not raw_total:
        return 1
    direct_error = abs(raw_total - primary_total) / primary_total
    thousands_error = abs((raw_total * 1000) - primary_total) / primary_total
    return 1000 if thousands_error < direct_error and thousands_error < 0.05 else 1


def parse_table_value_total(xml: bytes) -> Any:
    root = ET.fromstring(xml)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "tableValueTotal":
            return decimal_or_zero(node.text)
    return decimal_or_zero(0)


def sum_info_table_values(xml: bytes) -> Any:
    root = ET.fromstring(xml)
    total = decimal_or_zero(0)
    for row in root.findall("n:infoTable", INFO_NS):
        total += decimal_or_zero(row.findtext("n:value", default="0", namespaces=INFO_NS))
    return total


def resolve_holding_symbol(
    issuer: str,
    title_class: str,
    cusip: str,
    symbol_map: dict[str, str],
    issuer_map: dict[str, str],
) -> str:
    if symbol := symbol_map.get(cusip) or symbol_map.get(cusip.upper()):
        return symbol
    normalized = normalize_issuer_for_symbol(f"{issuer} {title_class}")
    for alias, symbol in sorted(issuer_map.items(), key=lambda item: len(item[0]), reverse=True):
        if normalize_issuer_for_symbol(alias) in normalized:
            return symbol
    return ""


def normalize_issuer_for_symbol(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


DEFAULT_CUSIP_SYMBOL_MAP = {
    "00187Y100": "APG",
    "00508Y102": "AYI",
    "00827B106": "AFRM",
    "009066101": "ABNB",
    "00971T101": "AKAM",
    "00091G104": "ACVA",
    "008073108": "AVAV",
    "01609W102": "BABA",
    "02005N100": "ALLY",
    "025816109": "AXP",
    "037833100": "AAPL",
    "04626A103": "ALAB",
    "060505104": "BAC",
    "136375102": "CNI",
    "14040H105": "COF",
    "16935C109": "CHYM",
    "166764100": "CVX",
    "171779309": "CIEN",
    "184496107": "CLH",
    "18467V109": "YOU",
    "191216100": "KO",
    "21036P108": "STZ",
    "20464U100": "COMP",
    "235851102": "DHR",
    "253393102": "DKS",
    "217204106": "CPRT",
    "23918K108": "DVA",
    "247361702": "DAL",
    "278768106": "SATS",
    "36165L108": "GDS",
    "37637K108": "GTLB",
    "443573100": "HUBS",
    "538034109": "LYV",
    "55306N104": "MKSI",
    "303250104": "FICO",
    "31488V107": "FERG",
    "369604301": "GE",
    "500754106": "KHC",
    "501044101": "KR",
    "526057104": "LEN",
    "530909100": "LLYVA",
    "530909308": "LLYVK",
    "546347105": "LPX",
    "57636Q104": "MA",
    "58507V107": "MDLN",
    "654902204": "NOK",
    "615369105": "MCO",
    "629377508": "NRG",
    "650111107": "NYT",
    "670346105": "NUE",
    "67103H107": "ORLY",
    "674599105": "OXY",
    "74276R102": "PRVA",
    "697435105": "PANW",
    "751212101": "RL",
    "75524B104": "RBC",
    "775133101": "ROG",
    "77311W101": "RKT",
    "776696106": "ROP",
    "781154109": "RBRK",
    "78409V104": "SPGI",
    "79589L106": "IOT",
    "816850101": "SMTC",
    "829933100": "SIRI",
    "91332U101": "U",
    "918284100": "VSEC",
    "922417100": "VECO",
    "92343E102": "VRSN",
    "93403J106": "WRBY",
    "94419L101": "W",
    "95082P105": "WCC",
    "974155103": "WING",
    "983793100": "XPO",
    "98980L101": "ZM",
    "036752103": "ELV",
    "032654105": "ADI",
    "03213A104": "AMPL",
    "031652100": "AMKR",
    "090043100": "BILL",
    "09857L108": "BKNG",
    "907818108": "UNP",
    "879369106": "TFX",
    "26969P108": "EXP",
    "372460105": "GPC",
    "464286772": "EWY",
    "464287655": "IWM",
    "82452J109": "FOUR",
    "82509L107": "SHOP",
    "G25457105": "CRDO",
    "G3168P101": "FER",
    "G3323L100": "FN",
    "G39387108": "GFS",
    "G0403H108": "AON",
    "G4412G101": "HLF",
    "G61188127": "LBTYK",
    "G7709Q104": "RPRX",
    "G96629103": "WTW",
    "M2197Q107": "CLBT",
    "M6191J100": "FROG",
    "M8744T106": "TBLA",
    "M98068105": "WIX",
    "N00985106": "AER",
    "N3168P101": "FER",
    "N6596X109": "NXPI",
    "N97284108": "NBIS",
    "007903107": "AMD",
    "02079K107": "GOOG",
    "02079K305": "GOOGL",
    "023135106": "AMZN",
    "03831W108": "APP",
    "093712107": "BE",
    "11135F101": "AVGO",
    "18452B209": "CLSK",
    "23804L103": "DDOG",
    "21873S108": "CRWV",
    "30303M102": "META",
    "36828A101": "GEV",
    "458140100": "INTC",
    "55024U109": "LITE",
    "573874104": "MRVL",
    "594918104": "MSFT",
    "595112103": "MU",
    "60937P106": "MDB",
    "68389X105": "ORCL",
    "69608A108": "PLTR",
    "79466L302": "CRM",
    "81762P102": "NOW",
    "833445109": "SNOW",
    "874039100": "TSM",
    "21874A106": "CORZ",
    "Q4982L109": "IREN",
    "038169207": "APLD",
    "038222105": "AMAT",
    "17253J106": "CIFR",
    "26884L109": "EQT",
    "19247G107": "COHR",
    "67066G104": "NVDA",
    "92840M102": "VST",
    "21037T109": "CEG",
    "87422Q109": "TLN",
    "92537N108": "VRT",
    "042068205": "ARM",
    "040413106": "ANET",
    "N07059210": "ASML",
    "G11448100": "BTDR",
    "09173B107": "BITF",
    "512807306": "LRCX",
    "90353T100": "UBER",
    "G29183103": "ETN",
    "29444U700": "EQIX",
    "80004C200": "SNDK",
    "64110L106": "NFLX",
    "L8681T102": "SPOT",
    "632307104": "NTRA",
    "G6683N103": "NU",
    "75734B100": "RDDT",
    "871607107": "SNPS",
    "31946M103": "FCNCA",
    "92826C839": "V",
    "576323109": "MTZ",
    "747525103": "QCOM",
    "767292105": "RIOT",
    "25809K105": "DASH",
    "770700102": "HOOD",
    "29355A107": "ENPH",
    "70450Y103": "PYPL",
    "49845K101": "KVYO",
    "922475108": "VEEV",
    "05464C101": "AXON",
    "78781J109": "SAIL",
    "81764X103": "TTAN",
    "22266T109": "CPNG",
    "146869102": "CVNA",
    "26603R106": "DUOL",
    "852234103": "XYZ",
    "43300A203": "HLT",
    "76131D103": "QSR",
    "169656105": "CMG",
    "44267T102": "HHH",
    "11271J107": "BN",
    "654106103": "NKE",
    "457669307": "INSM",
    "984245100": "YPF",
    "464286400": "EWZ",
    "G0896C103": "TBBB",
    "013872106": "AA",
    "N62509109": "NAMS",
    "81141R100": "SE",
    "861012102": "STM",
    "980745103": "WWD",
    "881624209": "TEVA",
    "77543R102": "ROKU",
    "68404L201": "OPCH",
    "G25508105": "CRH",
    "76155X100": "RVMD",
    "518415104": "LSCC",
    "910047109": "UAL",
    "444859102": "HUM",
    "929740108": "WAB",
    "90138F102": "TWLO",
    "466313103": "JBL",
    "84265V105": "SCCO",
    "G54950103": "LIN",
    "G7997R103": "STX",
    "185899101": "CLF",
    "67080N101": "NUVB",
    "07782B104": "BLTE",
    "58733R102": "MELI",
    "722304102": "PDD",
    "461202103": "INTU",
    "771049103": "RBLX",
}

DEFAULT_ISSUER_SYMBOL_MAP = {
    "ACV AUCTIONS": "ACVA",
    "AEROVIRONMENT": "AVAV",
    "AKAMAI": "AKAM",
    "AMKOR": "AMKR",
    "AMPLITUDE": "AMPL",
    "ANALOG DEVICES": "ADI",
    "AON": "AON",
    "CELLEBRITE": "CLBT",
    "CHIME FINL": "CHYM",
    "COMPASS INC": "COMP",
    "DANAHER": "DHR",
    "DICKS SPORTING": "DKS",
    "ELEVANCE HEALTH": "ELV",
    "GLOBALFOUNDRIES": "GFS",
    "HUBSPOT": "HUBS",
    "JFROG": "FROG",
    "LIBERTY GLOBAL": "LBTYK",
    "LIVE NATION": "LYV",
    "MKS": "MKSI",
    "NEBIUS": "NBIS",
    "NOKIA": "NOK",
    "NXP SEMICONDUCTORS": "NXPI",
    "PALO ALTO NETWORKS": "PANW",
    "RALPH LAUREN": "RL",
    "ROGERS CORP": "ROG",
    "RUBRIK": "RBRK",
    "SAMSARA": "IOT",
    "SEMTECH": "SMTC",
    "TABOOLA": "TBLA",
    "TELEFLEX": "TFX",
    "TESLA": "TSLA",
    "VEECO": "VECO",
    "WESCO": "WCC",
    "WILLIS TOWERS": "WTW",
    "WINGSTOP": "WING",
    "AERCAP": "AER",
    "AFFIRM": "AFRM",
    "AIRBNB": "ABNB",
    "ALIBABA": "BABA",
    "ALLY FINL": "ALLY",
    "AMERICAN EXPRESS": "AXP",
    "API GROUP": "APG",
    "APPLE": "AAPL",
    "ASTERA LABS": "ALAB",
    "BANK AMERICA": "BAC",
    "BERKSHIRE HATHAWAY": "BRK.B",
    "BIO-TECHNE": "TECH",
    "CANADIAN NATL RY": "CNI",
    "CAPITAL ONE": "COF",
    "CHEVRON": "CVX",
    "CIENA": "CIEN",
    "CLEAN HARBORS": "CLH",
    "CLEAR SECURE": "YOU",
    "COCA COLA": "KO",
    "CONSTELLATION BRANDS": "STZ",
    "COPART": "CPRT",
    "COSTAR": "CSGP",
    "DAVITA": "DVA",
    "DELTA AIR": "DAL",
    "ECHOSTAR": "SATS",
    "ENTEGRIS": "ENTG",
    "FAIR ISAAC": "FICO",
    "FABRINET": "FN",
    "FERROVIAL": "FER",
    "FERGUSON": "FERG",
    "GE AEROSPACE": "GE",
    "ISHARES INC MSCI STH KOR ETF": "EWY",
    "ISHARES TR RUSSELL 2000 ETF": "IWM",
    "KKR": "KKR",
    "KRAFT HEINZ": "KHC",
    "KROGER": "KR",
    "LENNAR": "LEN",
    "LIBERTY LIVE HOLDINGS INC COM SER A": "LLYVA",
    "LIBERTY LIVE HOLDINGS INC COM SHS SER C": "LLYVK",
    "LOUISIANA PAC": "LPX",
    "MASTERCARD": "MA",
    "MEDLINE": "MDLN",
    "MOODYS": "MCO",
    "NEW YORK TIMES": "NYT",
    "NRG ENERGY": "NRG",
    "NUCOR": "NUE",
    "OCCIDENTAL PETE": "OXY",
    "OREILLY AUTOMOTIVE": "ORLY",
    "PRIVIA HEALTH": "PRVA",
    "QNITY ELECTRONICS": "Q",
    "RBC BEARINGS": "RBC",
    "ROCKET COS": "RKT",
    "ROPER TECHNOLOGIES": "ROP",
    "ROYALTY PHARMA": "RPRX",
    "SHIFT4 PMTS": "FOUR",
    "SIRIUSXM": "SIRI",
    "S&P GLOBAL": "SPGI",
    "UNITY SOFTWARE": "U",
    "VERISIGN": "VRSN",
    "VSE CORP": "VSEC",
    "WARBY PARKER": "WRBY",
    "WAYFAIR": "W",
    "WHIRLPOOL": "WHR",
    "XPO": "XPO",
    "ZOOM COMMUNICATIONS": "ZM",
    "ADVANCED MICRO DEVICES": "AMD",
    "ALPHABET INC CL A": "GOOGL",
    "ALPHABET INC CAP STK CL A": "GOOGL",
    "AMAZON COM INC": "AMZN",
    "ALCOA": "AA",
    "APPLIED MATLS": "AMAT",
    "APPLOVIN CORP": "APP",
    "ARISTA NETWORKS": "ANET",
    "ARM HOLDINGS PLC": "ARM",
    "ASML HOLDING": "ASML",
    "BLOCK INC": "XYZ",
    "BLOOM ENERGY": "BE",
    "BELITE BIO": "BLTE",
    "BROADCOM": "AVGO",
    "BROOKFIELD CORP": "BN",
    "CANADIAN PACIFIC": "CP",
    "CARVANA": "CVNA",
    "CELESTICA": "CLS",
    "CHIPOTLE": "CMG",
    "CLEANSPARK": "CLSK",
    "COHERENT": "COHR",
    "CONSTELLATION ENERGY": "CEG",
    "COUPANG": "CPNG",
    "CORE SCIENTIFIC": "CORZ",
    "COREWEAVE": "CRWV",
    "CIPHER MINING": "CIFR",
    "DATADOG": "DDOG",
    "DOORDASH": "DASH",
    "DUOLINGO": "DUOL",
    "EATON CORP": "ETN",
    "ENPHASE ENERGY": "ENPH",
    "EQUINIX": "EQIX",
    "EQT CORP": "EQT",
    "EXPAND ENERGY": "EXE",
    "FIRST CTZNS BANCSHARES": "FCNCA",
    "GE VERNOVA": "GEV",
    "GLOBAL E ONLINE": "GLBE",
    "GLOBAL X FDS GB MSCI AR ETF": "ARGT",
    "HILTON WORLDWIDE": "HLT",
    "HOWARD HUGHES": "HHH",
    "HUMANA": "HUM",
    "INSMED": "INSM",
    "INTEL": "INTC",
    "INTUIT": "INTU",
    "IREN": "IREN",
    "ISHARES INC MSCI BRAZIL ETF": "EWZ",
    "ISHARES S&P GSCI COMMODITY": "GSG",
    "JABIL": "JBL",
    "KLAVIYO": "KVYO",
    "LUMENTUM": "LITE",
    "LAM RESEARCH": "LRCX",
    "MARVELL": "MRVL",
    "MASTEC": "MTZ",
    "MERCADOLIBRE": "MELI",
    "META PLATFORMS": "META",
    "MICRON": "MU",
    "MICROSOFT": "MSFT",
    "MONGODB": "MDB",
    "NATERA": "NTRA",
    "NEWAMSTERDAM PHARMA": "NAMS",
    "NETFLIX": "NFLX",
    "NIKE": "NKE",
    "NU HLDGS": "NU",
    "NVIDIA": "NVDA",
    "ORACLE": "ORCL",
    "OPTION CARE HEALTH": "OPCH",
    "PALANTIR": "PLTR",
    "PAYPAL": "PYPL",
    "PDD HOLDINGS": "PDD",
    "QUALCOMM": "QCOM",
    "REDDIT": "RDDT",
    "REVOLUTION MEDICINES": "RVMD",
    "RESTAURANT BRANDS": "QSR",
    "RIOT PLATFORMS": "RIOT",
    "ROBLOX": "RBLX",
    "ROKU": "ROKU",
    "SALESFORCE": "CRM",
    "SANDISK": "SNDK",
    "SAILPOINT": "SAIL",
    "SERVICENOW": "NOW",
    "SERVICETITAN": "TTAN",
    "SEA LTD": "SE",
    "SNOWFLAKE": "SNOW",
    "SPOTIFY": "SPOT",
    "STMICROELECTRONICS": "STM",
    "SOUTHERN COPPER": "SCCO",
    "SYNOPSYS": "SNPS",
    "TAIWAN SEMICONDUCTOR": "TSM",
    "TALEN ENERGY": "TLN",
    "UBER TECHNOLOGIES": "UBER",
    "UNITED AIRLS": "UAL",
    "VEEVA": "VEEV",
    "VISA": "V",
    "VERTIV": "VRT",
    "VISTRA": "VST",
    "WABTEC": "WAB",
    "WOODWARD": "WWD",
    "YPF SOCIEDAD": "YPF",
}
