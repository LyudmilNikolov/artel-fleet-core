"""Generate real mock files (images + PDFs) and upload to MinIO for existing Document records.

This script reads Document records from DB and generates actual files for them,
uploading to MinIO so presigned URLs return real content.
"""

import asyncio
import io
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.i18n import _
from app.models.document import Document, DocumentType, EntityType
from app.models.driver import Driver
from app.models.vehicle import Vehicle
from app.utils.formatting import money
from app.utils.s3 import get_s3_client

# Colors for vehicle photos
VEHICLE_COLORS = [
    (41, 128, 185),  # Blue
    (44, 62, 80),  # Dark gray
    (192, 57, 43),  # Red
    (39, 174, 96),  # Green
    (142, 68, 173),  # Purple
    (243, 156, 18),  # Orange
    (127, 140, 141),  # Silver
    (236, 240, 241),  # White-ish
    (52, 73, 94),  # Navy
    (22, 160, 133),  # Teal
]

DOC_BG_COLORS = {
    DocumentType.INVOICE: (255, 248, 240),
    DocumentType.ACT: (240, 248, 255),
    DocumentType.CONTRACT: (248, 248, 255),
    DocumentType.LICENSE: (255, 255, 240),
    DocumentType.MEDICAL: (240, 255, 240),
    DocumentType.INSURANCE: (255, 240, 245),
    DocumentType.SCAN: (245, 245, 245),
    DocumentType.OTHER: (250, 250, 250),
}


def generate_vehicle_photo(brand: str, model: str, plate: str, width: int = 800, height: int = 600) -> bytes:
    """Generate a stylized vehicle placeholder image."""
    bg_color = random.choice(VEHICLE_COLORS)
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Draw a simple car silhouette
    car_y = height * 0.45
    # Body
    body_color = tuple(max(0, c - 30) for c in bg_color)
    draw.rounded_rectangle([width * 0.15, car_y, width * 0.85, car_y + height * 0.25], radius=15, fill=body_color)
    # Roof
    roof_color = tuple(max(0, c - 50) for c in bg_color)
    draw.rounded_rectangle([width * 0.28, car_y - height * 0.15, width * 0.72, car_y + 5], radius=12, fill=roof_color)
    # Windows
    win_color = (200, 220, 240)
    draw.rounded_rectangle([width * 0.30, car_y - height * 0.13, width * 0.48, car_y - 2], radius=6, fill=win_color)
    draw.rounded_rectangle([width * 0.52, car_y - height * 0.13, width * 0.70, car_y - 2], radius=6, fill=win_color)
    # Wheels
    wheel_y = car_y + height * 0.25 - 5
    for wx in [width * 0.28, width * 0.72]:
        draw.ellipse([wx - 25, wheel_y - 10, wx + 25, wheel_y + 30], fill=(30, 30, 30))
        draw.ellipse([wx - 15, wheel_y, wx + 15, wheel_y + 20], fill=(80, 80, 80))

    # Headlights
    draw.ellipse([width * 0.15 - 5, car_y + 15, width * 0.15 + 20, car_y + 40], fill=(255, 255, 200))
    draw.ellipse([width * 0.85 - 20, car_y + 15, width * 0.85 + 5, car_y + 40], fill=(255, 100, 100))

    # Text: brand + model
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        font_plate = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font_large = ImageFont.load_default()
        font_small = font_large
        font_plate = font_large

    text_color = (255, 255, 255)
    shadow = (0, 0, 0)

    # Brand + model at top
    title = f"{brand} {model}"
    bbox = draw.textbbox((0, 0), title, font=font_large)
    tx = (width - (bbox[2] - bbox[0])) // 2
    draw.text((tx + 2, 22), title, fill=shadow, font=font_large)
    draw.text((tx, 20), title, fill=text_color, font=font_large)

    # License plate at bottom (plate-style box)
    plate_w, plate_h = 220, 40
    px = (width - plate_w) // 2
    py = height - 70
    draw.rounded_rectangle([px, py, px + plate_w, py + plate_h], radius=6, fill=(255, 255, 255))
    draw.rounded_rectangle([px + 1, py + 1, px + plate_w - 1, py + plate_h - 1], radius=5, outline=(0, 0, 0), width=2)
    pbbox = draw.textbbox((0, 0), plate, font=font_plate)
    ptx = px + (plate_w - (pbbox[2] - pbbox[0])) // 2
    pty = py + (plate_h - (pbbox[3] - pbbox[1])) // 2 - 2
    draw.text((ptx, pty), plate, fill=(0, 0, 0), font=font_plate)

    # "FleetCore" watermark
    draw.text((15, height - 30), "FleetCore", fill=(*text_color[:3], 128), font=font_small)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def generate_document_image(
    doc_type: DocumentType, title: str, details: list[str], width: int = 600, height: int = 800
) -> bytes:
    """Generate a document-style placeholder image (like a scanned doc)."""
    bg = DOC_BG_COLORS.get(doc_type, (250, 250, 250))
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except OSError:
        font_title = ImageFont.load_default()
        font_body = font_title
        font_small = font_title

    # Header bar
    header_color = {
        DocumentType.INVOICE: (46, 125, 50),
        DocumentType.ACT: (21, 101, 192),
        DocumentType.CONTRACT: (106, 27, 154),
        DocumentType.LICENSE: (230, 126, 34),
        DocumentType.MEDICAL: (0, 150, 136),
        DocumentType.INSURANCE: (183, 28, 28),
    }.get(doc_type, (100, 100, 100))

    draw.rectangle([0, 0, width, 60], fill=header_color)
    draw.text(
        (20, 15),
        f"FleetCore — {_('document_type.' + doc_type.value, 'bg').upper()}",
        fill=(255, 255, 255),
        font=font_title,
    )

    # Title
    draw.text((30, 80), title, fill=(33, 33, 33), font=font_title)

    # Horizontal line
    draw.line([(30, 115), (width - 30, 115)], fill=(180, 180, 180), width=1)

    # Details
    y = 135
    for line in details:
        draw.text((30, y), line, fill=(66, 66, 66), font=font_body)
        y += 28

    # Stamp circle (for official look)
    stamp_x, stamp_y = width - 130, height - 180
    draw.ellipse([stamp_x, stamp_y, stamp_x + 100, stamp_y + 100], outline=header_color, width=2)
    draw.ellipse([stamp_x + 5, stamp_y + 5, stamp_x + 95, stamp_y + 95], outline=header_color, width=1)
    # stamp text
    st_bbox = draw.textbbox((0, 0), "ОДОБРЕНО", font=font_small)
    stw = st_bbox[2] - st_bbox[0]
    draw.text((stamp_x + (100 - stw) // 2, stamp_y + 35), "ОДОБРЕНО", fill=header_color, font=font_small)

    # Signature line
    draw.line([(30, height - 80), (200, height - 80)], fill=(100, 100, 100), width=1)
    draw.text((30, height - 75), "Подпис", fill=(150, 150, 150), font=font_small)

    # Date
    draw.text(
        (30, height - 40),
        f"Дата: 2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
        fill=(100, 100, 100),
        font=font_small,
    )

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def generate_driver_photo(name: str, employee_id: str, width: int = 400, height: int = 500) -> bytes:
    """Generate a driver badge-style placeholder photo."""
    img = Image.new("RGB", (width, height), (240, 242, 245))
    draw = ImageDraw.Draw(img)

    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_id = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except OSError:
        font_name = ImageFont.load_default()
        font_id = font_name
        font_small = font_name

    # Header
    draw.rectangle([0, 0, width, 50], fill=(33, 150, 243))
    draw.text((15, 12), "FleetCore ID", fill=(255, 255, 255), font=font_name)

    # Avatar circle
    cx, cy, r = width // 2, 160, 70
    colors = [(41, 128, 185), (39, 174, 96), (142, 68, 173), (230, 126, 34), (192, 57, 43)]
    avatar_color = random.choice(colors)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=avatar_color)
    # Initials
    initials = "".join(w[0].upper() for w in name.split()[:2])
    ibbox = draw.textbbox((0, 0), initials, font=font_name)
    iw, ih = ibbox[2] - ibbox[0], ibbox[3] - ibbox[1]
    draw.text((cx - iw // 2, cy - ih // 2 - 5), initials, fill=(255, 255, 255), font=font_name)

    # Name
    nbbox = draw.textbbox((0, 0), name, font=font_name)
    nw = nbbox[2] - nbbox[0]
    draw.text(((width - nw) // 2, cy + r + 20), name, fill=(33, 33, 33), font=font_name)

    # Employee ID
    eid_text = f"ID: {employee_id}"
    ebbox = draw.textbbox((0, 0), eid_text, font=font_id)
    ew = ebbox[2] - ebbox[0]
    draw.text(((width - ew) // 2, cy + r + 55), eid_text, fill=(100, 100, 100), font=font_id)

    # Footer
    draw.rectangle([0, height - 35, width, height], fill=(245, 245, 245))
    draw.text((15, height - 28), "Corporate Fleet Management", fill=(150, 150, 150), font=font_small)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def seed_files():
    print("Generating and uploading real files to MinIO...")
    s3 = get_s3_client()

    async with AsyncSessionLocal() as db:
        # Load all documents
        result = await db.execute(select(Document).order_by(Document.uploaded_at))
        documents = result.scalars().all()
        print(f"Found {len(documents)} document records in DB")

        # Load vehicles for context
        v_result = await db.execute(select(Vehicle))
        vehicles = {v.id: v for v in v_result.scalars().all()}

        # Load drivers for context
        d_result = await db.execute(select(Driver))
        drivers = {d.id: d for d in d_result.scalars().all()}

        uploaded = 0
        errors = 0

        for doc in documents:
            try:
                if doc.type == DocumentType.PHOTO and doc.entity_type == EntityType.VEHICLE:
                    v = vehicles.get(doc.entity_id)
                    if v:
                        data = generate_vehicle_photo(v.brand, v.model, v.license_plate)
                    else:
                        data = generate_vehicle_photo("Vehicle", "Photo", "000 XXX 00")
                    mime = "image/jpeg"

                elif doc.type in (DocumentType.LICENSE, DocumentType.MEDICAL) and doc.entity_type == EntityType.DRIVER:
                    d = drivers.get(doc.entity_id)
                    if d:
                        data = generate_driver_photo(d.full_name, d.employee_id or "N/A")
                    else:
                        data = generate_driver_photo("Driver", "N/A")
                    mime = "image/jpeg"

                elif doc.type == DocumentType.INVOICE:
                    data = generate_document_image(
                        DocumentType.INVOICE,
                        f"Фактура № {random.randint(1000, 9999)}",
                        [
                            f"Доставчик: {'AutoMaster Service' if random.random() > 0.5 else 'Toyota Center Almaty'}",
                            f"Дата: 2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
                            "Сума: " + money(Decimal(random.randint(10, 500)) * 10, "bg"),
                            "",
                            "Извършени дейности:",
                            f"  1. {'Смяна на масло и филтри' if random.random() > 0.5 else 'Диагностика на двигателя'}",
                            f"  2. {'Смяна на спирачни накладки' if random.random() > 0.5 else 'Баланс на колела'}",
                            "",
                            "Общо с включен ДДС",
                        ],
                    )
                    mime = "image/jpeg"

                elif doc.type == DocumentType.ACT:
                    data = generate_document_image(
                        DocumentType.ACT,
                        f"Протокол за извършени дейности № {random.randint(100, 9999)}",
                        [
                            f"Изпълнител: {random.choice(['Bosch Car Service', 'FIT Auto', 'TechnoService KZ'])}",
                            "Възложител: FleetCore",
                            f"Дата: 2025-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}",
                            "",
                            "Дейностите са изпълнени изцяло.",
                            "Няма възражения за качеството.",
                        ],
                    )
                    mime = "image/jpeg"

                elif doc.type == DocumentType.CONTRACT:
                    data = generate_document_image(
                        DocumentType.CONTRACT,
                        f"Договор № {random.choice(['INS', 'LEA', 'SRV'])}-{random.randint(10000, 99999)}",
                        [
                            f"Контрагент: {random.choice(['Nomad Insurance', 'Halyk Leasing', 'Eurasia Insurance'])}",
                            "Период: 12 месеца",
                            "Сума: " + money(Decimal(random.randint(100, 5000)) * 10, "bg"),
                            "",
                            "Предмет на договора:",
                            f"  {random.choice(['Застраховка Каско', 'Лизинг на превозно средство', 'Техническо обслужване'])}",
                            "",
                            "Страните постигнаха съгласие.",
                        ],
                    )
                    mime = "image/jpeg"

                elif doc.type == DocumentType.INSURANCE:
                    data = generate_document_image(
                        DocumentType.INSURANCE,
                        f"Полица № {random.randint(100000, 999999)}",
                        [
                            f"Застраховател: {random.choice(['Nomad Insurance', 'Eurasia', 'Halyk Insurance'])}",
                            f"Тип: {random.choice(['КАСКО', 'ОСАГО', 'ДСАГО'])}",
                            "Срок: 12 месеца",
                            "Премия: " + money(Decimal(random.randint(50, 500)) * 10, "bg"),
                            "",
                            "Застраховано превозно средство:",
                            "  Регистрационен номер: по регистър",
                        ],
                    )
                    mime = "image/jpeg"

                else:
                    # Generic scan / other
                    data = generate_document_image(
                        doc.type,
                        f"Документ: {doc.filename}",
                        [
                            f"Тип: {doc.type.value}",
                            f"Качен: {doc.uploaded_at}",
                            f"Размер: {doc.size_bytes} байта",
                        ],
                    )
                    mime = "image/jpeg"

                # Upload to MinIO at the existing s3_key
                from io import BytesIO

                s3.client.upload_fileobj(
                    BytesIO(data),
                    s3.bucket,
                    doc.s3_key,
                    ExtraArgs={"ContentType": mime},
                )

                # Update size in DB
                doc.size_bytes = len(data)
                doc.mime_type = mime

                uploaded += 1
                if uploaded % 50 == 0:
                    print(f"  Uploaded {uploaded}/{len(documents)}...")

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Error for doc {doc.id}: {e}")

        await db.commit()
        print(f"\nDone! Uploaded {uploaded} files, {errors} errors.")


if __name__ == "__main__":
    asyncio.run(seed_files())
