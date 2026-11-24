"""Create a demo kiosk with 1 printer x 8 compartments (the M7 hardware design) and a demo user.

Run from the backend folder:  python -m scripts.seed
Prints the kiosk API key once; keep it for the kiosk agent (M2).
"""
import os

from sqlalchemy import select

from app.db import SessionLocal
from app.main import init_db
from app.models import Kiosk, User
from app.security import hash_password
from app.services.kiosks import add_printer, create_kiosk


def main() -> None:
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(Kiosk.id).where(Kiosk.name == "Demo kiosk")):
            print("demo data already exists")
            return
        # PL_DEMO_KIOSK_KEY lets docker-compose start the simulated kiosk with a known key.
        kiosk, api_key = create_kiosk(db, "Demo kiosk", "Main building, ground floor",
                                      api_key=os.environ.get("PL_DEMO_KIOSK_KEY") or None)
        add_printer(db, kiosk.id, "Printer A", "printer_a", duplex=True, compartments=8)
        if not db.scalar(select(User.id).where(User.email == "demo@example.com")):
            db.add(User(email="demo@example.com", password_hash=hash_password("demo12345")))
        db.commit()
        print(f"kiosk id {kiosk.id}, API key (shown once): {api_key}")
        print("demo user: demo@example.com / demo12345")


if __name__ == "__main__":
    main()
