"""Generate seed data for FleetCore: 500+ vehicles, drivers, maintenance, expenses, contracts,
mileage logs, documents, notifications, audit logs.

WARNING: This script creates demo users with a shared password (SeedPass!2024).
         Use only in development/staging environments.
"""

import asyncio
import random
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.i18n import _
from app.models.audit_log import AuditAction, AuditLog
from app.models.contract import Contract, ContractStatus, ContractType, PaymentFrequency
from app.models.document import Document, DocumentType, EntityType
from app.models.driver import Driver, DriverStatus
from app.models.expense import Currency, Expense, ExpenseCategory
from app.models.maintenance import (
    MaintenanceRecord,
    MaintenanceStatus,
    MaintenanceType,
)
from app.models.mileage import MileageLog, MileageSource
from app.models.notification import Notification, NotificationPreference, NotificationType
from app.models.user import User, UserRole
from app.models.vehicle import (
    BodyType,
    FuelType,
    TransmissionType,
    Vehicle,
    VehicleStatus,
)
from app.utils.security import hash_password

# ---- Configuration ----
NUM_VEHICLES = 520
NUM_DRIVERS = 220
NUM_MAINTENANCE = 1200
NUM_EXPENSES = 5500
NUM_CONTRACTS = 350
NUM_MILEAGE_LOGS = 3000
NUM_DOCUMENTS = 800
NUM_NOTIFICATIONS = 600
NUM_AUDIT_LOGS = 2000

# Kazakhstan regions (2-digit codes)
KZ_REGIONS = [
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
]
KZ_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Popular car brands/models in KZ
VEHICLES_DATA = [
    ("Toyota", "Camry", "sedan", "gasoline", 2.5),
    ("Toyota", "Corolla", "sedan", "gasoline", 1.6),
    ("Toyota", "Land Cruiser 200", "suv", "diesel", 4.5),
    ("Toyota", "Land Cruiser 300", "suv", "diesel", 3.3),
    ("Toyota", "RAV4", "suv", "gasoline", 2.0),
    ("Toyota", "Hilux", "pickup", "diesel", 2.4),
    ("Toyota", "Fortuner", "suv", "diesel", 2.8),
    ("Hyundai", "Tucson", "suv", "gasoline", 2.0),
    ("Hyundai", "Santa Fe", "suv", "diesel", 2.2),
    ("Hyundai", "Sonata", "sedan", "gasoline", 2.0),
    ("Hyundai", "Elantra", "sedan", "gasoline", 1.6),
    ("Hyundai", "Accent", "sedan", "gasoline", 1.4),
    ("Hyundai", "Creta", "suv", "gasoline", 1.6),
    ("Kia", "Sportage", "suv", "gasoline", 2.0),
    ("Kia", "K5", "sedan", "gasoline", 2.0),
    ("Kia", "Cerato", "sedan", "gasoline", 1.6),
    ("Kia", "Seltos", "suv", "gasoline", 1.6),
    ("Kia", "Carnival", "minivan", "diesel", 2.2),
    ("Chevrolet", "Cobalt", "sedan", "gasoline", 1.5),
    ("Chevrolet", "Tracker", "suv", "gasoline", 1.0),
    ("Chevrolet", "Monza", "sedan", "gasoline", 1.5),
    ("Lada", "Vesta", "sedan", "gasoline", 1.6),
    ("Lada", "Granta", "sedan", "gasoline", 1.6),
    ("Lada", "Niva Travel", "suv", "gasoline", 1.7),
    ("Skoda", "Octavia", "sedan", "gasoline", 1.4),
    ("Skoda", "Rapid", "sedan", "gasoline", 1.6),
    ("Volkswagen", "Polo", "sedan", "gasoline", 1.6),
    ("Volkswagen", "Tiguan", "suv", "gasoline", 2.0),
    ("Nissan", "Qashqai", "suv", "gasoline", 2.0),
    ("Nissan", "X-Trail", "suv", "gasoline", 2.5),
    ("Mitsubishi", "Outlander", "suv", "gasoline", 2.4),
    ("Mitsubishi", "Pajero Sport", "suv", "diesel", 2.4),
    ("Renault", "Duster", "suv", "gasoline", 2.0),
    ("Renault", "Logan", "sedan", "gasoline", 1.6),
    ("Mercedes-Benz", "E-Class", "sedan", "gasoline", 2.0),
    ("Mercedes-Benz", "Sprinter", "van", "diesel", 2.1),
    ("BMW", "5 Series", "sedan", "gasoline", 2.0),
    ("Isuzu", "D-Max", "pickup", "diesel", 3.0),
    ("GAZ", "Gazel Next", "van", "diesel", 2.8),
    ("GAZ", "Gazel Business", "van", "diesel", 2.8),
    ("Ford", "Transit", "van", "diesel", 2.2),
    ("Hyundai", "HD78", "truck", "diesel", 3.9),
    ("KAMAZ", "5490", "truck", "diesel", 11.9),
    ("MAN", "TGS", "truck", "diesel", 10.5),
    ("PAZ", "4234", "bus", "diesel", 4.4),
    ("Yutong", "ZK6122H9", "bus", "diesel", 8.9),
]

COLORS = ["White", "Black", "Silver", "Gray", "Blue", "Red", "Green", "Brown", "Beige"]
DEPARTMENTS = [
    "Administration",
    "Logistics",
    "Sales",
    "Engineering",
    "IT",
    "HR",
    "Finance",
    "Operations",
    "Marketing",
    "Supply Chain",
    "Transport Pool",
    "Executive",
    "Field Service",
    "Maintenance Team",
    "Security",
]

SERVICE_PROVIDERS = [
    "Toyota Center Almaty",
    "Hyundai Auto Kazakhstan",
    "Kia Motors Nur-Sultan",
    "AutoMaster Service",
    "Bosch Car Service",
    "Shell Helix Center",
    "Mobil 1 Center",
    "Total Service Station",
    "FIT Auto",
    "German Auto Service",
    "Japan Car Service",
    "Korean Auto Center",
    "Quick Lube Express",
    "TechnoService KZ",
    "Euro Auto Repair",
]

FUEL_STATIONS = [
    "KazMunayGas",
    "Helios",
    "Sinooil",
    "Royal Petrol",
    "Shell",
    "Total Energies",
    "AQNIET",
    "Gazpromneft",
    "Rompetrol",
    "Lukoil",
]

# User-Agent strings for audit logs
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile",
]

# Document file extensions and mime types
DOC_TEMPLATES = {
    DocumentType.PHOTO: [
        ("photo_{n}.jpg", "image/jpeg", (500_000, 5_000_000)),
        ("img_{n}.png", "image/png", (800_000, 8_000_000)),
    ],
    DocumentType.SCAN: [
        ("scan_{n}.pdf", "application/pdf", (200_000, 3_000_000)),
        ("scan_{n}.jpg", "image/jpeg", (300_000, 2_000_000)),
    ],
    DocumentType.INVOICE: [
        ("invoice_{n}.pdf", "application/pdf", (100_000, 1_000_000)),
    ],
    DocumentType.ACT: [
        ("act_{n}.pdf", "application/pdf", (150_000, 2_000_000)),
    ],
    DocumentType.CONTRACT: [
        ("contract_{n}.pdf", "application/pdf", (200_000, 5_000_000)),
    ],
    DocumentType.LICENSE: [
        ("license_{n}.jpg", "image/jpeg", (300_000, 2_000_000)),
        ("license_{n}.pdf", "application/pdf", (200_000, 1_500_000)),
    ],
    DocumentType.MEDICAL: [
        ("medical_cert_{n}.pdf", "application/pdf", (150_000, 1_000_000)),
        ("medical_cert_{n}.jpg", "image/jpeg", (300_000, 2_000_000)),
    ],
    DocumentType.INSURANCE: [
        ("insurance_policy_{n}.pdf", "application/pdf", (200_000, 3_000_000)),
    ],
}

# Stable catalog keys for newly generated recipient-localized notifications.
NOTIFICATION_VARIANTS = {
    NotificationType.MAINTENANCE_REMINDER: 3,
    NotificationType.CONTRACT_EXPIRY: 2,
    NotificationType.LICENSE_EXPIRY: 2,
    NotificationType.MEDICAL_EXPIRY: 2,
    NotificationType.MILEAGE_ALERT: 1,
    NotificationType.BUDGET_ALERT: 1,
    NotificationType.SYSTEM: 4,
}


def notification_templates(locale, notification_type):
    """Resolve application-owned seed text explicitly for its recipient."""
    locale = locale if locale in ("bg", "en") else "bg"
    prefix = "seed.notification." + notification_type.value
    return [
        (_(f"{prefix}.{index}.title", locale), _(f"{prefix}.{index}.message", locale))
        for index in range(NOTIFICATION_VARIANTS[notification_type])
    ]


# Kazakh first names (male and female)
KZ_FIRST_NAMES = [
    "Arman",
    "Nursultan",
    "Daulet",
    "Aibek",
    "Kanat",
    "Marat",
    "Ruslan",
    "Yerlan",
    "Bauyrzhan",
    "Serik",
    "Aidar",
    "Nurzhan",
    "Timur",
    "Daniyar",
    "Zhenis",
    "Askar",
    "Bolat",
    "Erbol",
    "Samat",
    "Kairat",
    "Amir",
    "Aliya",
    "Dinara",
    "Zarina",
    "Aigul",
    "Madina",
    "Saltanat",
    "Asel",
    "Gulnara",
    "Zhanna",
    "Raushan",
    "Ainur",
    "Kamila",
    "Laura",
    "Dana",
    "Aizhan",
    "Moldir",
    "Symbat",
    "Nurgul",
    "Akmaral",
    "Zhaniya",
]

# Kazakh last names
KZ_LAST_NAMES = [
    "Aitbayev",
    "Baymuratov",
    "Dzhakishev",
    "Ermagambetov",
    "Iskakov",
    "Kenzhebayev",
    "Muratov",
    "Nazarbayev",
    "Omarov",
    "Sarsenbayev",
    "Tokayev",
    "Urazov",
    "Zhanseitov",
    "Abdrakhmanov",
    "Bektemirov",
    "Dosymov",
    "Gabbasov",
    "Kairatov",
    "Mustafin",
    "Nurpeisov",
    "Rakhimov",
    "Serikbayev",
    "Tursynbayev",
    "Yergaliyev",
    "Zhumatov",
    "Almukhamedov",
    "Batyrbekov",
    "Galimov",
    "Kadyrbekov",
    "Mazhitov",
]


def gen_plate() -> str:
    """Generate a Kazakhstan-format license plate: 123 ABC 01."""
    digits = f"{random.randint(1, 999):03d}"
    letters = "".join(random.choices(KZ_LETTERS, k=3))
    region = random.choice(KZ_REGIONS)
    return f"{digits} {letters} {region}"


def gen_vin() -> str:
    """Generate a random 17-character VIN."""
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ1234567890"
    return "".join(random.choices(chars, k=17))


def rand_date(start: date, end: date) -> date:
    delta = (end - start).days
    if delta <= 0:
        return start
    return start + timedelta(days=random.randint(0, delta))


def rand_datetime(start: date, end: date) -> datetime:
    """Generate a random timezone-aware datetime between two dates."""
    d = rand_date(start, end)
    return datetime(d.year, d.month, d.day, random.randint(6, 22), random.randint(0, 59), tzinfo=UTC)


def rand_ip() -> str:
    """Generate a random private IP address."""
    return f"192.168.{random.randint(1, 254)}.{random.randint(1, 254)}"


async def seed():
    print("Starting seed data generation...")
    used_plates = set()
    used_vins = set()

    async with AsyncSessionLocal() as db:
        # Clean existing seed data (keep admin user)
        print("Cleaning existing data...")
        from sqlalchemy import delete

        # Delete in order respecting FK constraints
        for tbl in [
            AuditLog,
            Notification,
            NotificationPreference,
            Document,
            MileageLog,
            Expense,
            MaintenanceRecord,
            Contract,
            Vehicle,
            Driver,
        ]:
            await db.execute(delete(tbl))
        # Delete non-admin users
        await db.execute(delete(User).where(User.role != UserRole.ADMIN))
        await db.flush()
        print("Existing data cleaned.")

        # Get admin user for created_by references
        admin_result = await db.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
        admin = admin_result.scalar_one_or_none()
        admin_id = admin.id if admin else None

        # ---- Create additional users ----
        print("Creating users...")
        users = []
        for i in range(5):
            user = User(
                email=f"manager{i + 1}@fleetcore.kz",
                username=f"manager{i + 1}",
                full_name=f"{random.choice(KZ_FIRST_NAMES)} {random.choice(KZ_LAST_NAMES)}",
                hashed_password=hash_password("SeedPass!2024"),
                role=UserRole.FLEET_MANAGER,
                is_active=True,
                language="bg",
            )
            db.add(user)
            users.append(user)
        for i in range(3):
            user = User(
                email=f"viewer{i + 1}@fleetcore.kz",
                username=f"viewer{i + 1}",
                full_name=f"{random.choice(KZ_FIRST_NAMES)} {random.choice(KZ_LAST_NAMES)}",
                hashed_password=hash_password("SeedPass!2024"),
                role=UserRole.VIEWER,
                is_active=True,
                language="bg",
            )
            db.add(user)
            users.append(user)
        await db.flush()
        all_user_ids = [u.id for u in users]
        if admin_id:
            all_user_ids.append(admin_id)

        # ---- Create drivers ----
        print(f"Creating {NUM_DRIVERS} drivers...")
        drivers = []
        for i in range(NUM_DRIVERS):
            today = date.today()
            license_exp = rand_date(today - timedelta(days=30), today + timedelta(days=365 * 3))
            medical_exp = rand_date(today - timedelta(days=30), today + timedelta(days=365 * 2))
            hire_d = rand_date(date(2015, 1, 1), today - timedelta(days=30))
            status = random.choices(
                [DriverStatus.ACTIVE, DriverStatus.ON_LEAVE, DriverStatus.TERMINATED],
                weights=[85, 10, 5],
            )[0]
            fname = random.choice(KZ_FIRST_NAMES)
            lname = random.choice(KZ_LAST_NAMES)
            driver = Driver(
                full_name=f"{fname} {lname}",
                employee_id=f"EMP-{1000 + i}",
                phone=f"+7 {random.randint(700, 778)} {random.randint(100, 999)} {random.randint(10, 99)} {random.randint(10, 99)}",
                email=f"{fname.lower()}.{lname.lower()}@fleetcore.kz",
                license_number=f"{random.randint(10, 99)}{random.randint(100000, 999999):06d}",
                license_category=random.choice(["B", "B, C", "B, C, D", "B, C, CE", "D"]),
                license_expiry=license_exp,
                medical_expiry=medical_exp,
                hire_date=hire_d,
                department=random.choice(DEPARTMENTS),
                status=status,
            )
            db.add(driver)
            drivers.append(driver)
        await db.flush()
        active_driver_ids = [d.id for d in drivers if d.status == DriverStatus.ACTIVE]

        # ---- Create vehicles ----
        print(f"Creating {NUM_VEHICLES} vehicles...")
        vehicles = []
        for i in range(NUM_VEHICLES):
            brand, model, body, fuel, engine = random.choice(VEHICLES_DATA)
            year = random.randint(2016, 2025)
            purchase = rand_date(date(year, 1, 1), min(date(year, 12, 31), date.today()))
            status = random.choices(
                [
                    VehicleStatus.ACTIVE,
                    VehicleStatus.IN_MAINTENANCE,
                    VehicleStatus.DECOMMISSIONED,
                    VehicleStatus.RESERVED,
                ],
                weights=[80, 10, 5, 5],
            )[0]
            plate = gen_plate()
            while plate in used_plates:
                plate = gen_plate()
            used_plates.add(plate)
            vin = gen_vin()
            while vin in used_vins:
                vin = gen_vin()
            used_vins.add(vin)

            transmission = random.choice(list(TransmissionType))
            mileage = random.randint(5000, 350000) if year < 2025 else random.randint(0, 30000)
            price = Decimal(random.randint(6000, 90000))
            assigned_driver = (
                random.choice(active_driver_ids) if status == VehicleStatus.ACTIVE and random.random() > 0.3 else None
            )

            vehicle = Vehicle(
                license_plate=plate,
                vin=vin,
                brand=brand,
                model=model,
                year=year,
                color=random.choice(COLORS),
                body_type=BodyType(body),
                fuel_type=FuelType(fuel),
                engine_volume=engine,
                transmission=transmission,
                seats=random.choice([2, 5, 7, 8, 15, 25, 45])
                if body in ("bus", "minivan")
                else random.choice([2, 5, 7]),
                purchase_date=purchase,
                purchase_price=price,
                current_mileage=mileage,
                status=status,
                assigned_driver_id=assigned_driver,
                department=random.choice(DEPARTMENTS),
                notes=None,
            )
            db.add(vehicle)
            vehicles.append(vehicle)
        await db.flush()
        vehicle_ids = [v.id for v in vehicles]
        active_vehicle_ids = [
            v.id for v in vehicles if v.status in (VehicleStatus.ACTIVE, VehicleStatus.IN_MAINTENANCE)
        ]

        # ---- Create maintenance records ----
        print(f"Creating {NUM_MAINTENANCE} maintenance records...")
        maintenance_objs = []
        maint_titles = {
            MaintenanceType.SCHEDULED_SERVICE: [
                "ТО-1: замена масла и фильтров",
                "ТО-2: полное обслуживание",
                "ТО-3: замена масла, фильтров, свечей",
                "ТО-4: капитальное обслуживание",
                "Замена масла и масляного фильтра",
                "Плановое ТО по регламенту",
            ],
            MaintenanceType.REPAIR: [
                "Ремонт подвески",
                "Замена тормозных колодок",
                "Замена аккумулятора",
                "Ремонт генератора",
                "Замена ремня ГРМ",
                "Ремонт рулевой рейки",
                "Замена радиатора",
            ],
            MaintenanceType.INSPECTION: [
                "Технический осмотр",
                "Диагностика двигателя",
                "Проверка тормозной системы",
                "Инспекция ходовой части",
            ],
            MaintenanceType.TIRE_CHANGE: [
                "Сезонная замена шин (зимние)",
                "Сезонная замена шин (летние)",
                "Замена повреждённой шины",
                "Балансировка колёс",
            ],
            MaintenanceType.BODY_REPAIR: [
                "Покраска бампера",
                "Удаление вмятин",
                "Замена лобового стекла",
                "Полировка кузова",
            ],
            MaintenanceType.RECALL: [
                "Отзывная кампания: подушки безопасности",
                "Отзывная кампания: обновление ПО",
            ],
        }

        today = date.today()
        for _ in range(NUM_MAINTENANCE):
            mtype = random.choice(list(MaintenanceType))
            title = random.choice(maint_titles[mtype])
            scheduled = rand_date(today - timedelta(days=365), today + timedelta(days=90))
            status = random.choices(
                [
                    MaintenanceStatus.SCHEDULED,
                    MaintenanceStatus.IN_PROGRESS,
                    MaintenanceStatus.COMPLETED,
                    MaintenanceStatus.CANCELLED,
                ],
                weights=[25, 10, 55, 10],
            )[0]
            completed = (
                scheduled + timedelta(days=random.randint(0, 5)) if status == MaintenanceStatus.COMPLETED else None
            )
            cost = Decimal(random.randint(10, 1000)) if status == MaintenanceStatus.COMPLETED else None
            vid = random.choice(active_vehicle_ids)

            record = MaintenanceRecord(
                vehicle_id=vid,
                type=mtype,
                title=title,
                description=f"Описание работ: {title}",
                status=status,
                scheduled_date=scheduled,
                completed_date=completed,
                mileage_at_service=random.randint(10000, 300000),
                next_service_mileage=random.randint(10000, 300000) + 10000 if random.random() > 0.5 else None,
                next_service_date=scheduled + timedelta(days=random.randint(90, 365))
                if random.random() > 0.5
                else None,
                cost=cost,
                service_provider=random.choice(SERVICE_PROVIDERS),
                performed_by=f"{random.choice(KZ_FIRST_NAMES)} {random.choice(KZ_LAST_NAMES)}",
                created_by=random.choice(all_user_ids) if all_user_ids else None,
            )
            db.add(record)
            maintenance_objs.append(record)
        await db.flush()

        # ---- Create expenses ----
        print(f"Creating {NUM_EXPENSES} expense records...")
        for _ in range(NUM_EXPENSES):
            category = random.choices(
                list(ExpenseCategory),
                weights=[40, 10, 15, 8, 5, 5, 5, 3, 5, 4],
            )[0]
            vid = random.choice(active_vehicle_ids)
            exp_date = rand_date(today - timedelta(days=365), today)

            if category == ExpenseCategory.FUEL:
                liters = round(random.uniform(20, 80), 1)
                price_per_liter = Decimal(random.randint(120, 200)) / 100
                amount = (price_per_liter * Decimal(str(liters))).quantize(Decimal("0.01"))
                fuel_type = random.choice(["AI-92", "AI-95", "AI-98", "DT"])
                mileage_at_refuel = random.randint(10000, 300000)
                vendor = random.choice(FUEL_STATIONS)
            else:
                liters = None
                price_per_liter = None
                amount = Decimal(random.randint(10, 3000))
                fuel_type = None
                mileage_at_refuel = None
                vendor = (
                    random.choice(SERVICE_PROVIDERS + FUEL_STATIONS)
                    if category in (ExpenseCategory.PARTS, ExpenseCategory.SERVICE)
                    else None
                )

            expense = Expense(
                vehicle_id=vid,
                driver_id=random.choice(active_driver_ids) if random.random() > 0.3 else None,
                category=category,
                amount=amount,
                currency=Currency.EUR,
                date=exp_date,
                description=f"{category.value}: запись расхода",
                vendor=vendor,
                fuel_liters=liters,
                fuel_price_per_liter=price_per_liter,
                fuel_type=fuel_type,
                mileage_at_refuel=mileage_at_refuel,
                created_by=random.choice(all_user_ids) if all_user_ids else None,
            )
            db.add(expense)

        # ---- Create contracts ----
        print(f"Creating {NUM_CONTRACTS} contracts...")
        contractors = {
            ContractType.LEASING: ["KazFinance Leasing", "Halyk Leasing", "BCC Leasing", "Freedom Finance Leasing"],
            ContractType.RENTAL: ["AutoRent KZ", "Hertz Kazakhstan", "Europcar KZ", "Sixt Kazakhstan"],
            ContractType.INSURANCE_CASCO: [
                "Nomad Insurance",
                "Eurasia Insurance",
                "Halyk Insurance",
                "Freedom Insurance",
            ],
            ContractType.INSURANCE_OSAGO: [
                "Nomad Insurance",
                "Eurasia Insurance",
                "Halyk Insurance",
                "KompetenzGarant",
            ],
            ContractType.WARRANTY: ["Toyota Kazakhstan", "Hyundai Kazakhstan", "Kia Kazakhstan", "Official Dealer"],
            ContractType.SERVICE_CONTRACT: SERVICE_PROVIDERS[:8],
        }

        contract_objs = []
        for _ in range(NUM_CONTRACTS):
            ctype = random.choice(list(ContractType))
            start = rand_date(today - timedelta(days=365 * 2), today)
            duration = random.choice([90, 180, 365, 365 * 2, 365 * 3])
            end = start + timedelta(days=duration)
            status = ContractStatus.ACTIVE if end >= today else ContractStatus.EXPIRED
            if status == ContractStatus.ACTIVE and random.random() < 0.1:
                status = ContractStatus.PENDING_RENEWAL

            contract = Contract(
                vehicle_id=random.choice(vehicle_ids),
                type=ctype,
                contractor=random.choice(contractors[ctype]),
                contract_number=f"{ctype.value[:3].upper()}-{random.randint(10000, 99999)}",
                start_date=start,
                end_date=end,
                amount=Decimal(random.randint(100, 10000)),
                payment_frequency=random.choice(list(PaymentFrequency)),
                status=status,
                auto_renew=random.random() > 0.7,
                notes=None,
                created_by=random.choice(all_user_ids) if all_user_ids else None,
            )
            db.add(contract)
            contract_objs.append(contract)
        await db.flush()
        contract_ids = [c.id for c in contract_objs]

        # ---- Create mileage logs ----
        print(f"Creating {NUM_MILEAGE_LOGS} mileage log records...")
        # Distribute logs across vehicles — each vehicle gets several readings
        mileage_per_vehicle = {}
        for v in vehicles:
            mileage_per_vehicle[v.id] = {
                "purchase_date": v.purchase_date or date(v.year, 1, 1),
                "current": v.current_mileage,
            }

        logs_created = 0
        # First pass: give each active vehicle at least a few readings
        for v in vehicles:
            if v.status == VehicleStatus.DECOMMISSIONED:
                continue
            num_logs = random.randint(3, 12)
            if logs_created + num_logs > NUM_MILEAGE_LOGS:
                num_logs = max(1, NUM_MILEAGE_LOGS - logs_created)

            purchase_d = v.purchase_date or date(v.year, 6, 1)
            total_km = v.current_mileage
            if total_km <= 0:
                continue

            # Generate sorted timestamps and monotonically increasing mileage values
            log_dates = sorted([rand_date(purchase_d, today) for _ in range(num_logs)])
            # Generate increasing mileage points
            points = sorted(random.sample(range(1000, max(1001, total_km)), min(num_logs, max(1, total_km - 1000))))
            # If fewer points than dates, pad with the last value
            while len(points) < len(log_dates):
                points.append(points[-1] + random.randint(100, 2000))

            for i, (log_d, km_val) in enumerate(zip(log_dates, points)):
                source = random.choices(
                    [MileageSource.MANUAL, MileageSource.OBD, MileageSource.GPS],
                    weights=[60, 20, 20],
                )[0]
                mlog = MileageLog(
                    vehicle_id=v.id,
                    recorded_by=random.choice(all_user_ids) if all_user_ids else None,
                    value=km_val,
                    source=source,
                    recorded_at=rand_datetime(log_d, log_d),
                    notes=random.choice([None, None, None, "Показания одометра", "Плановая запись", "При заправке"]),
                )
                db.add(mlog)
                logs_created += 1
                if logs_created >= NUM_MILEAGE_LOGS:
                    break
            if logs_created >= NUM_MILEAGE_LOGS:
                break

        # Fill remaining slots with random logs
        remaining_vehicles = [
            v for v in vehicles if v.status != VehicleStatus.DECOMMISSIONED and v.current_mileage > 1000
        ]
        while logs_created < NUM_MILEAGE_LOGS and remaining_vehicles:
            v = random.choice(remaining_vehicles)
            purchase_d = v.purchase_date or date(v.year, 6, 1)
            mlog = MileageLog(
                vehicle_id=v.id,
                recorded_by=random.choice(all_user_ids) if all_user_ids else None,
                value=random.randint(1000, v.current_mileage),
                source=random.choice(list(MileageSource)),
                recorded_at=rand_datetime(purchase_d, today),
                notes=None,
            )
            db.add(mlog)
            logs_created += 1

        # ---- Create documents ----
        print(f"Creating {NUM_DOCUMENTS} document records...")
        doc_counter = 0

        # Vehicle photos (~200 docs: 2-4 photos per ~60-70 vehicles)
        photo_vehicles = random.sample(vehicles, min(70, len(vehicles)))
        for v in photo_vehicles:
            num_photos = random.randint(2, 4)
            for _ in range(num_photos):
                if doc_counter >= NUM_DOCUMENTS:
                    break
                tpl = random.choice(DOC_TEMPLATES[DocumentType.PHOTO])
                doc = Document(
                    entity_type=EntityType.VEHICLE,
                    entity_id=v.id,
                    type=DocumentType.PHOTO,
                    filename=tpl[0].format(n=doc_counter),
                    s3_key=f"vehicles/{v.id}/photos/{uuid4().hex[:12]}{tpl[0].split('.')[-1]}",
                    mime_type=tpl[1],
                    size_bytes=random.randint(tpl[2][0], tpl[2][1]),
                    uploaded_by=random.choice(all_user_ids) if all_user_ids else None,
                    uploaded_at=rand_datetime(v.purchase_date or date(v.year, 1, 1), today),
                )
                db.add(doc)
                doc_counter += 1
            if doc_counter >= NUM_DOCUMENTS:
                break

        # Driver license scans (~150 docs)
        for d in random.sample(drivers, min(150, len(drivers))):
            if doc_counter >= NUM_DOCUMENTS:
                break
            doc_type = random.choice([DocumentType.LICENSE, DocumentType.MEDICAL])
            tpl = random.choice(DOC_TEMPLATES[doc_type])
            doc = Document(
                entity_type=EntityType.DRIVER,
                entity_id=d.id,
                type=doc_type,
                filename=tpl[0].format(n=doc_counter),
                s3_key=f"drivers/{d.id}/{doc_type.value}/{uuid4().hex[:12]}.{tpl[0].split('.')[-1]}",
                mime_type=tpl[1],
                size_bytes=random.randint(tpl[2][0], tpl[2][1]),
                uploaded_by=random.choice(all_user_ids) if all_user_ids else None,
                uploaded_at=rand_datetime(d.hire_date or date(2020, 1, 1), today),
            )
            db.add(doc)
            doc_counter += 1

        # Maintenance invoices/acts (~200 docs)
        completed_maint_ids = [m.id for m in maintenance_objs if m.status == MaintenanceStatus.COMPLETED]
        for mid in random.sample(completed_maint_ids, min(200, len(completed_maint_ids))):
            if doc_counter >= NUM_DOCUMENTS:
                break
            doc_type = random.choice([DocumentType.INVOICE, DocumentType.ACT])
            tpl = random.choice(DOC_TEMPLATES[doc_type])
            doc = Document(
                entity_type=EntityType.MAINTENANCE,
                entity_id=mid,
                type=doc_type,
                filename=tpl[0].format(n=doc_counter),
                s3_key=f"maintenance/{mid}/{uuid4().hex[:12]}.{tpl[0].split('.')[-1]}",
                mime_type=tpl[1],
                size_bytes=random.randint(tpl[2][0], tpl[2][1]),
                uploaded_by=random.choice(all_user_ids) if all_user_ids else None,
                uploaded_at=rand_datetime(today - timedelta(days=365), today),
            )
            db.add(doc)
            doc_counter += 1

        # Contract scans (~100 docs)
        for cid in random.sample(contract_ids, min(100, len(contract_ids))):
            if doc_counter >= NUM_DOCUMENTS:
                break
            tpl = random.choice(DOC_TEMPLATES[DocumentType.CONTRACT])
            doc = Document(
                entity_type=EntityType.CONTRACT,
                entity_id=cid,
                type=DocumentType.CONTRACT,
                filename=tpl[0].format(n=doc_counter),
                s3_key=f"contracts/{cid}/{uuid4().hex[:12]}.pdf",
                mime_type=tpl[1],
                size_bytes=random.randint(tpl[2][0], tpl[2][1]),
                uploaded_by=random.choice(all_user_ids) if all_user_ids else None,
                uploaded_at=rand_datetime(today - timedelta(days=365 * 2), today),
            )
            db.add(doc)
            doc_counter += 1

        # Expense receipts (fill remaining)
        while doc_counter < NUM_DOCUMENTS:
            doc_type = random.choice(
                [DocumentType.SCAN, DocumentType.INVOICE, DocumentType.INSURANCE, DocumentType.OTHER]
            )
            templates = DOC_TEMPLATES.get(doc_type, DOC_TEMPLATES[DocumentType.SCAN])
            tpl = random.choice(templates)
            doc = Document(
                entity_type=random.choice([EntityType.EXPENSE, EntityType.VEHICLE]),
                entity_id=uuid4(),
                type=doc_type,
                filename=tpl[0].format(n=doc_counter),
                s3_key=f"documents/{uuid4().hex[:16]}.{tpl[0].split('.')[-1]}",
                mime_type=tpl[1],
                size_bytes=random.randint(tpl[2][0], tpl[2][1]),
                uploaded_by=random.choice(all_user_ids) if all_user_ids else None,
                uploaded_at=rand_datetime(today - timedelta(days=365), today),
            )
            db.add(doc)
            doc_counter += 1

        # ---- Create notifications ----
        print(f"Creating {NUM_NOTIFICATIONS} notifications...")
        # Gather some data for realistic notification messages
        sample_plates = [v.license_plate for v in random.sample(vehicles, min(50, len(vehicles)))]
        sample_driver_names = [d.full_name for d in random.sample(drivers, min(50, len(drivers)))]

        notification_users = {u.id: u for u in users}
        if admin is not None:
            notification_users[admin.id] = admin
        for i in range(NUM_NOTIFICATIONS):
            ntype = random.choices(
                list(NotificationType),
                weights=[25, 20, 15, 10, 10, 5, 15],  # maintenance most common
            )[0]
            recipient = random.choice(list(notification_users.values()))
            templates = notification_templates(recipient.language, ntype)
            title_tpl, msg_tpl = random.choice(templates)

            # Fill template placeholders
            plate = random.choice(sample_plates)
            driver_name = random.choice(sample_driver_names)
            days_val = random.choice([1, 3, 7, 14, 30])
            future_date = (today + timedelta(days=days_val)).strftime("%d.%m.%Y")
            past_date = (today - timedelta(days=random.randint(1, 60))).strftime("%d.%m.%Y")

            fmt_kwargs = {
                "days": days_val,
                "plate": plate,
                "date": future_date if random.random() > 0.3 else past_date,
                "driver": driver_name,
                "contract": f"INS-{random.randint(10000, 99999)}",
                "mileage": f"{random.randint(50, 300) * 1000:,}",
                "delta": random.randint(1100, 3000),
                "amount": f"{random.randint(50, 500) * 1000:,}",
                "total": f"{random.randint(200, 2000) * 1000:,}",
                "user": f"{random.choice(KZ_FIRST_NAMES)} {random.choice(KZ_LAST_NAMES)}",
                "role": random.choice(["fleet_manager", "viewer", "driver"]),
                "version": f"2.{random.randint(1, 9)}.{random.randint(0, 15)}",
                "size": f"{random.randint(1, 50)} GB",
                "file": f"import_{random.randint(1000, 9999)}.xlsx",
                "count": random.randint(50, 5000),
            }

            try:
                title = title_tpl.format(**fmt_kwargs)
                message = msg_tpl.format(**fmt_kwargs)
            except KeyError:
                title = title_tpl
                message = msg_tpl

            # Determine entity reference
            entity_type_str = None
            entity_id_val = None
            if ntype in (
                NotificationType.MAINTENANCE_REMINDER,
                NotificationType.MILEAGE_ALERT,
                NotificationType.BUDGET_ALERT,
            ):
                entity_type_str = "vehicle"
                entity_id_val = random.choice(vehicle_ids)
            elif ntype == NotificationType.CONTRACT_EXPIRY:
                entity_type_str = "contract"
                entity_id_val = random.choice(contract_ids)
            elif ntype in (NotificationType.LICENSE_EXPIRY, NotificationType.MEDICAL_EXPIRY):
                entity_type_str = "driver"
                entity_id_val = random.choice([d.id for d in drivers])

            notif = Notification(
                user_id=recipient.id,
                title=title,
                message=message,
                type=ntype,
                is_read=random.random() < 0.65,  # 65% read
                entity_type=entity_type_str,
                entity_id=entity_id_val,
                created_at=rand_datetime(today - timedelta(days=90), today),
            )
            db.add(notif)

        # ---- Create notification preferences ----
        print("Creating notification preferences...")
        for uid in all_user_ids:
            pref = NotificationPreference(
                user_id=uid,
                email_enabled=random.random() > 0.1,  # 90% have email on
                telegram_enabled=random.random() > 0.6,  # 40% have telegram on
                telegram_chat_id=str(random.randint(100000000, 999999999)) if random.random() > 0.6 else None,
                preferences={
                    "maintenance_reminder": True,
                    "contract_expiry": True,
                    "license_expiry": random.random() > 0.2,
                    "medical_expiry": random.random() > 0.2,
                    "mileage_alert": random.random() > 0.3,
                    "budget_alert": random.random() > 0.4,
                    "system": True,
                },
            )
            db.add(pref)

        # ---- Create audit logs ----
        print(f"Creating {NUM_AUDIT_LOGS} audit log records...")
        entity_types_for_audit = ["vehicle", "driver", "maintenance", "expense", "contract", "user", "document"]
        entity_ids_pool = vehicle_ids + [d.id for d in drivers] + contract_ids

        for i in range(NUM_AUDIT_LOGS):
            action = random.choices(
                list(AuditAction),
                weights=[25, 35, 5, 15, 10, 10],  # update most common, delete rare
            )[0]
            entity_type = random.choice(entity_types_for_audit)
            entity_id = random.choice(entity_ids_pool) if action != AuditAction.LOGIN else None

            # Generate realistic changes for create/update actions
            changes = None
            if action == AuditAction.CREATE:
                if entity_type == "vehicle":
                    changes = {
                        "license_plate": gen_plate(),
                        "brand": random.choice(["Toyota", "Hyundai", "Kia"]),
                        "status": "active",
                    }
                elif entity_type == "driver":
                    changes = {
                        "full_name": f"{random.choice(KZ_FIRST_NAMES)} {random.choice(KZ_LAST_NAMES)}",
                        "status": "active",
                    }
                elif entity_type == "expense":
                    changes = {
                        "amount": str(random.randint(5000, 300000)),
                        "category": random.choice(["fuel", "parts", "service"]),
                    }
                else:
                    changes = {"action": "created"}
            elif action == AuditAction.UPDATE:
                if entity_type == "vehicle":
                    field = random.choice(["status", "current_mileage", "assigned_driver_id", "department", "notes"])
                    if field == "status":
                        changes = {
                            "old": {"status": "active"},
                            "new": {"status": random.choice(["in_maintenance", "reserved", "active"])},
                        }
                    elif field == "current_mileage":
                        old_km = random.randint(10000, 200000)
                        changes = {
                            "old": {"current_mileage": old_km},
                            "new": {"current_mileage": old_km + random.randint(500, 5000)},
                        }
                    else:
                        changes = {"old": {field: "old_value"}, "new": {field: "new_value"}}
                elif entity_type == "maintenance":
                    changes = {
                        "old": {"status": "scheduled"},
                        "new": {"status": random.choice(["in_progress", "completed"])},
                    }
                elif entity_type == "driver":
                    changes = {"old": {"status": "active"}, "new": {"status": random.choice(["on_leave", "active"])}}
                else:
                    changes = {"old": {}, "new": {}}
            elif action == AuditAction.DELETE:
                changes = {"deleted_entity": entity_type, "id": str(uuid4())}
            elif action == AuditAction.EXPORT:
                changes = {"format": random.choice(["xlsx", "csv"]), "records": random.randint(10, 5000)}
            # LOGIN/LOGOUT have no changes

            audit = AuditLog(
                user_id=random.choice(all_user_ids),
                action=action,
                entity_type=entity_type if action not in (AuditAction.LOGIN, AuditAction.LOGOUT) else "session",
                entity_id=entity_id,
                changes=changes,
                ip_address=rand_ip(),
                user_agent=random.choice(USER_AGENTS),
                timestamp=rand_datetime(today - timedelta(days=180), today),
            )
            db.add(audit)

        # ---- Commit all ----
        await db.commit()
        print("\nSeed data created successfully!")
        print(f"  Users:              {len(users)} new (+ existing)")
        print(f"  Drivers:            {NUM_DRIVERS}")
        print(f"  Vehicles:           {NUM_VEHICLES}")
        print(f"  Mileage logs:       {NUM_MILEAGE_LOGS}")
        print(f"  Maintenance:        {NUM_MAINTENANCE}")
        print(f"  Expenses:           {NUM_EXPENSES}")
        print(f"  Contracts:          {NUM_CONTRACTS}")
        print(f"  Documents:          {NUM_DOCUMENTS}")
        print(f"  Notifications:      {NUM_NOTIFICATIONS}")
        print(f"  Audit logs:         {NUM_AUDIT_LOGS}")
        total = (
            len(users)
            + NUM_DRIVERS
            + NUM_VEHICLES
            + NUM_MILEAGE_LOGS
            + NUM_MAINTENANCE
            + NUM_EXPENSES
            + NUM_CONTRACTS
            + NUM_DOCUMENTS
            + NUM_NOTIFICATIONS
            + NUM_AUDIT_LOGS
            + len(all_user_ids)
        )  # notif prefs
        print("  ─────────────────────────────")
        print(f"  TOTAL records:      ~{total}")


if __name__ == "__main__":
    asyncio.run(seed())
