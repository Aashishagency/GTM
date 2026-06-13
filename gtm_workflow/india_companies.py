"""Built-in directory of major Indian companies, tagged by industry and employee
size band. Used to power industry/size-based discovery on providers (like Hunter)
that can only look up people *within* a known company, not search firmographically.

Size bands match the UI: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000,
5001-10000, 10001+.  Sizes are approximate and editable — extend this list freely.
"""

COMPANIES = [
    # ── Automotive (OEMs) — dealer & customer loyalty heavy ──
    {"name": "Maruti Suzuki", "domain": "marutisuzuki.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Tata Motors", "domain": "tatamotors.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Mahindra & Mahindra", "domain": "mahindra.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Hyundai Motor India", "domain": "hyundai.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Hero MotoCorp", "domain": "heromotocorp.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Bajaj Auto", "domain": "bajajauto.com", "industry": "Automotive", "size": "10001+"},
    {"name": "TVS Motor", "domain": "tvsmotor.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Ashok Leyland", "domain": "ashokleyland.com", "industry": "Automotive", "size": "10001+"},
    {"name": "Royal Enfield", "domain": "royalenfield.com", "industry": "Automotive", "size": "5001-10000"},
    {"name": "Toyota Kirloskar Motor", "domain": "toyotabharat.com", "industry": "Automotive", "size": "5001-10000"},
    {"name": "Kia India", "domain": "kia.com", "industry": "Automotive", "size": "1001-5000"},
    {"name": "MG Motor India", "domain": "mgmotor.co.in", "industry": "Automotive", "size": "1001-5000"},

    # ── Auto Components ──
    {"name": "Bosch India", "domain": "bosch.in", "industry": "Auto Components", "size": "10001+"},
    {"name": "Motherson", "domain": "motherson.com", "industry": "Auto Components", "size": "10001+"},
    {"name": "MRF Tyres", "domain": "mrftyres.com", "industry": "Auto Components", "size": "10001+"},
    {"name": "Apollo Tyres", "domain": "apollotyres.com", "industry": "Auto Components", "size": "10001+"},
    {"name": "Bharat Forge", "domain": "bharatforge.com", "industry": "Auto Components", "size": "5001-10000"},
    {"name": "Exide Industries", "domain": "exideindustries.com", "industry": "Auto Components", "size": "5001-10000"},
    {"name": "Amara Raja", "domain": "amararaja.com", "industry": "Auto Components", "size": "5001-10000"},
    {"name": "CEAT", "domain": "ceat.com", "industry": "Auto Components", "size": "5001-10000"},

    # ── Consumer Durables / Electronics ──
    {"name": "Samsung India", "domain": "samsung.com", "industry": "Consumer Durables", "size": "10001+"},
    {"name": "LG Electronics India", "domain": "lg.com", "industry": "Consumer Durables", "size": "5001-10000"},
    {"name": "Voltas", "domain": "voltas.com", "industry": "Consumer Durables", "size": "5001-10000"},
    {"name": "Havells", "domain": "havells.com", "industry": "Consumer Durables", "size": "5001-10000"},
    {"name": "Blue Star", "domain": "bluestarindia.com", "industry": "Consumer Durables", "size": "5001-10000"},
    {"name": "Crompton Greaves Consumer", "domain": "crompton.co.in", "industry": "Consumer Durables", "size": "1001-5000"},
    {"name": "Bajaj Electricals", "domain": "bajajelectricals.com", "industry": "Consumer Durables", "size": "1001-5000"},
    {"name": "Whirlpool India", "domain": "whirlpoolindia.com", "industry": "Consumer Durables", "size": "1001-5000"},
    {"name": "Dixon Technologies", "domain": "dixoninfo.com", "industry": "Consumer Durables", "size": "5001-10000"},

    # ── Retail ──
    {"name": "Reliance Retail", "domain": "relianceretail.com", "industry": "Retail", "size": "10001+"},
    {"name": "Avenue Supermarts (DMart)", "domain": "dmart.in", "industry": "Retail", "size": "10001+"},
    {"name": "Trent (Westside)", "domain": "trentlimited.com", "industry": "Retail", "size": "10001+"},
    {"name": "Aditya Birla Fashion", "domain": "abfrl.com", "industry": "Retail", "size": "10001+"},
    {"name": "Titan Company", "domain": "titancompany.in", "industry": "Retail", "size": "10001+"},
    {"name": "Shoppers Stop", "domain": "shoppersstop.com", "industry": "Retail", "size": "5001-10000"},
    {"name": "Vishal Mega Mart", "domain": "vishalmegamart.com", "industry": "Retail", "size": "5001-10000"},
    {"name": "Spencer's Retail", "domain": "spencers.in", "industry": "Retail", "size": "5001-10000"},

    # ── FMCG ──
    {"name": "Hindustan Unilever", "domain": "hul.co.in", "industry": "FMCG", "size": "10001+"},
    {"name": "ITC", "domain": "itcportal.com", "industry": "FMCG", "size": "10001+"},
    {"name": "Nestlé India", "domain": "nestle.in", "industry": "FMCG", "size": "5001-10000"},
    {"name": "Britannia Industries", "domain": "britannia.co.in", "industry": "FMCG", "size": "5001-10000"},
    {"name": "Dabur India", "domain": "dabur.com", "industry": "FMCG", "size": "5001-10000"},
    {"name": "Godrej Consumer Products", "domain": "godrejcp.com", "industry": "FMCG", "size": "5001-10000"},
    {"name": "Marico", "domain": "marico.com", "industry": "FMCG", "size": "1001-5000"},
    {"name": "Parle Products", "domain": "parleproducts.com", "industry": "FMCG", "size": "5001-10000"},
    {"name": "Emami", "domain": "emamiltd.in", "industry": "FMCG", "size": "1001-5000"},

    # ── BFSI (Banking / Financial Services / Insurance) ──
    {"name": "HDFC Bank", "domain": "hdfcbank.com", "industry": "BFSI", "size": "10001+"},
    {"name": "ICICI Bank", "domain": "icicibank.com", "industry": "BFSI", "size": "10001+"},
    {"name": "Axis Bank", "domain": "axisbank.com", "industry": "BFSI", "size": "10001+"},
    {"name": "State Bank of India", "domain": "sbi.co.in", "industry": "BFSI", "size": "10001+"},
    {"name": "Kotak Mahindra Bank", "domain": "kotak.com", "industry": "BFSI", "size": "10001+"},
    {"name": "Bajaj Finserv", "domain": "bajajfinserv.in", "industry": "BFSI", "size": "10001+"},
    {"name": "HDFC Life", "domain": "hdfclife.com", "industry": "BFSI", "size": "5001-10000"},
    {"name": "SBI Life Insurance", "domain": "sbilife.co.in", "industry": "BFSI", "size": "5001-10000"},
    {"name": "ICICI Lombard", "domain": "icicilombard.com", "industry": "BFSI", "size": "5001-10000"},
    {"name": "Max Life Insurance", "domain": "maxlifeinsurance.com", "industry": "BFSI", "size": "5001-10000"},

    # ── Telecom ──
    {"name": "Bharti Airtel", "domain": "airtel.in", "industry": "Telecom", "size": "10001+"},
    {"name": "Reliance Jio", "domain": "jio.com", "industry": "Telecom", "size": "10001+"},
    {"name": "Vodafone Idea", "domain": "myvi.in", "industry": "Telecom", "size": "10001+"},

    # ── Pharma & Healthcare ──
    {"name": "Sun Pharma", "domain": "sunpharma.com", "industry": "Pharma & Healthcare", "size": "10001+"},
    {"name": "Cipla", "domain": "cipla.com", "industry": "Pharma & Healthcare", "size": "10001+"},
    {"name": "Dr. Reddy's", "domain": "drreddys.com", "industry": "Pharma & Healthcare", "size": "10001+"},
    {"name": "Lupin", "domain": "lupin.com", "industry": "Pharma & Healthcare", "size": "10001+"},
    {"name": "Apollo Hospitals", "domain": "apollohospitals.com", "industry": "Pharma & Healthcare", "size": "10001+"},
    {"name": "Mankind Pharma", "domain": "mankindpharma.com", "industry": "Pharma & Healthcare", "size": "5001-10000"},
    {"name": "Torrent Pharma", "domain": "torrentpharma.com", "industry": "Pharma & Healthcare", "size": "5001-10000"},

    # ── Travel & Hospitality ──
    {"name": "IndiGo", "domain": "goindigo.in", "industry": "Travel & Hospitality", "size": "10001+"},
    {"name": "Taj Hotels (IHCL)", "domain": "tajhotels.com", "industry": "Travel & Hospitality", "size": "10001+"},
    {"name": "OYO", "domain": "oyorooms.com", "industry": "Travel & Hospitality", "size": "5001-10000"},
    {"name": "MakeMyTrip", "domain": "makemytrip.com", "industry": "Travel & Hospitality", "size": "5001-10000"},
    {"name": "Yatra", "domain": "yatra.com", "industry": "Travel & Hospitality", "size": "1001-5000"},
    {"name": "EaseMyTrip", "domain": "easemytrip.com", "industry": "Travel & Hospitality", "size": "201-500"},

    # ── E-commerce / Internet ──
    {"name": "Flipkart", "domain": "flipkart.com", "industry": "E-commerce", "size": "10001+"},
    {"name": "Amazon India", "domain": "amazon.in", "industry": "E-commerce", "size": "10001+"},
    {"name": "Myntra", "domain": "myntra.com", "industry": "E-commerce", "size": "5001-10000"},
    {"name": "Nykaa", "domain": "nykaa.com", "industry": "E-commerce", "size": "1001-5000"},
    {"name": "Meesho", "domain": "meesho.com", "industry": "E-commerce", "size": "1001-5000"},
    {"name": "Snapdeal", "domain": "snapdeal.com", "industry": "E-commerce", "size": "501-1000"},

    # ── Oil & Energy ──
    {"name": "Reliance Industries", "domain": "ril.com", "industry": "Oil & Energy", "size": "10001+"},
    {"name": "Indian Oil", "domain": "iocl.com", "industry": "Oil & Energy", "size": "10001+"},
    {"name": "Bharat Petroleum", "domain": "bharatpetroleum.in", "industry": "Oil & Energy", "size": "10001+"},
    {"name": "HPCL", "domain": "hindustanpetroleum.com", "industry": "Oil & Energy", "size": "10001+"},
    {"name": "Adani Group", "domain": "adani.com", "industry": "Oil & Energy", "size": "10001+"},

    # ── Real Estate ──
    {"name": "DLF", "domain": "dlf.in", "industry": "Real Estate", "size": "5001-10000"},
    {"name": "Lodha Group", "domain": "lodhagroup.com", "industry": "Real Estate", "size": "5001-10000"},
    {"name": "Godrej Properties", "domain": "godrejproperties.com", "industry": "Real Estate", "size": "1001-5000"},
    {"name": "Prestige Group", "domain": "prestigeconstructions.com", "industry": "Real Estate", "size": "1001-5000"},
]


def all_industries() -> list[str]:
    seen, out = set(), []
    for c in COMPANIES:
        if c["industry"] not in seen:
            seen.add(c["industry"])
            out.append(c["industry"])
    return out


def find_companies(industries: list[str] = None, sizes: list[str] = None,
                   limit: int = 10) -> list[dict]:
    """Return directory companies matching the selected industries and size bands.
    Empty industries -> all industries; empty sizes -> all sizes."""
    ind = {i.strip().lower() for i in (industries or []) if i.strip()}
    szs = {s.strip() for s in (sizes or []) if s.strip()}
    out = []
    for c in COMPANIES:
        if ind and c["industry"].lower() not in ind:
            continue
        if szs and c["size"] not in szs:
            continue
        out.append(c)
        if len(out) >= max(1, limit):
            break
    return out
