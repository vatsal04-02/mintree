"""Demo catalogue: 12 products modelled on the public Mintree storefront (vmintree.in: names, pack sizes, shelf prices) plus the supplier / customer /
material / BOM / purchase-order constants the demo scenarios use. Names of suppliers and customers, costs, discounts and quantities are ILLUSTRATIVE."""
from decimal import Decimal

GST = Decimal("1.18")
TRADE = Decimal("0.65")            # B2B / salon price as a share of the shelf price (illustrative)

# name, shelf price incl. GST (storefront), pack, produced through the real chain?
CATALOGUE = [
    ("FG-DETAN-60", "100% ORGANIC Detan Face Mask 60 gm", 1423, True),
    ("FG-FALLSTOP-200", "Fall Stop Shampoo & Conditioner 200 ml each", 1846, False),
    ("FG-BW-OUD-200", "NOURISHING Oud Body Wash 200 ml", 991, True),
    ("FG-BW-FANTASEA-200", "NOURISHING Fantasea Body Wash 200 ml", 991, False),
    ("FG-BW-CALM-200", "NOURISHING Calm-A-Sutra Body Wash 200 ml", 991, False),
    ("FG-BW-OLIVE-200", "NOURISHING Olive Body Wash 200 ml", 991, False),
    ("FG-BB-OLIVE-150", "MOISTURIZING Olive Body Butter 150 gm", 991, True),
    ("FG-BS-OLIVE-150", "BRIGHTENING Olive Body Scrub 150 gm", 991, False),
    ("FG-BS-OUD-150", "BRIGHTENING Oud Body Scrub 150 gm", 991, True),
    ("FG-BS-FANTASEA-150", "BRIGHTENING Fantasea Body Scrub 150 gm", 991, False),
    ("FG-BL-OUD-200", "HYDRATING Oud Body Lotion 200 ml", 991, False),
    ("FG-HC-5X30", "MOISTURIZING HANDCREAMS (PACK OF 5) 30 gm each", 1999, False),
]
RAW = [  # code, name, kind, uom, gst
    ("RM-ALOE", "Aloe Vera Gel, organic", "RM", "KG"), ("RM-SURF", "Mild Surfactant Base (sulphate-free)", "RM", "KG"),
    ("RM-KAOLIN", "Organic Kaolin Clay", "RM", "KG"), ("RM-OLIVE", "Cold-pressed Olive Oil", "RM", "KG"),
    ("RM-SHEA", "Shea Butter, refined", "RM", "KG"), ("RM-OUD", "Oud Fragrance Oil", "RM", "KG"),
    ("PM-BOTTLE-200", "Body Wash Bottle 200 ml", "PM", "PCS"), ("PM-JAR-150", "Jar 150 gm, wooden cap", "PM", "PCS"), ("PM-JAR-60", "Jar 60 gm", "PM", "PCS"),
]
SUPPLIERS = [("DM-SUP-1", "Himalayan Botanicals Pvt Ltd", "RM", "Dehradun", "UK"), ("DM-SUP-2", "Coastal Oils & Butters LLP", "RM", "Kochi", "KL"), ("DM-SUP-3", "Prime Pack Industries", "PM", "Daman", "DD")]
CUSTOMERS = [("DM-CUS-1", "Glow Retail Pvt Ltd", "B2B", "Pune", "MH"), ("DM-CUS-2", "Aura Salon Chain", "DISTRIBUTOR", "Mumbai", "MH"),
             ("DM-CUS-3", "Mintree Online Store", "BRAND", "Gurugram", "HR"), ("DM-CUS-4", "Nykaa Marketplace", "BRAND", "Mumbai", "MH")]
# product -> (batch size in pcs, [(raw code, qty per batch)])
BOMS = {
    "FG-BW-OUD-200": (200, [("RM-SURF", "30"), ("RM-ALOE", "8"), ("RM-OUD", "0.6"), ("PM-BOTTLE-200", "200")]),
    "FG-BB-OLIVE-150": (150, [("RM-SHEA", "20"), ("RM-OLIVE", "10"), ("RM-ALOE", "2"), ("PM-JAR-150", "150")]),
    "FG-BS-OUD-150": (150, [("RM-KAOLIN", "15"), ("RM-OLIVE", "6"), ("RM-OUD", "0.4"), ("PM-JAR-150", "150")]),
    "FG-DETAN-60": (100, [("RM-KAOLIN", "8"), ("RM-ALOE", "3"), ("PM-JAR-60", "100")]),
}
POS = [  # supplier, [(raw code, qty, unit price)], action
    ("DM-SUP-1", [("RM-ALOE", "120", "85"), ("RM-SURF", "200", "140"), ("RM-KAOLIN", "80", "60")], "release"),
    ("DM-SUP-2", [("RM-OLIVE", "150", "420"), ("RM-SHEA", "100", "510"), ("RM-OUD", "10", "6200")], "release"),
    ("DM-SUP-3", [("PM-BOTTLE-200", "600", "14"), ("PM-JAR-150", "400", "22"), ("PM-JAR-60", "300", "18")], "release"),
    ("DM-SUP-2", [("RM-SHEA", "20", "505")], "quarantine"),      # received, QC decision still open
    ("DM-SUP-1", [("RM-SURF", "300", "138")], "approval"),       # submitted, waiting for approval
]


