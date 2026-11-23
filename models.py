"""
Sasta Sawari - domain model.

All business rules live here. The Flask layer (app.py) only translates
HTTP requests into calls on these objects.

OOP concepts used in this file (see MANUAL.md for the full map):
  Abstraction    -> User(ABC), FareStrategy(ABC)
  Inheritance    -> Rider(User), Captain(User), SaverFare/ExpressFare/ParcelFare(FareStrategy)
  Encapsulation  -> __balance, __status, __earnings are name-mangled private attributes
  Composition    -> Captain has a Bike; Ride has Location objects and a FareStrategy
  Polymorphism   -> Ride calls strategy.calculate_fare() without knowing which tier it is
"""

import itertools
import math
from abc import ABC, abstractmethod
from enum import Enum


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class SastaSawariError(Exception):
    """Base class for all business-rule violations."""


class InsufficientBalanceError(SastaSawariError):
    pass


class InvalidTransitionError(SastaSawariError):
    pass


class CaptainUnavailableError(SastaSawariError):
    pass


# ---------------------------------------------------------------------------
# Enumerations for fixed states
# ---------------------------------------------------------------------------
class RideStatus(Enum):
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CaptainStatus(Enum):
    AVAILABLE = "AVAILABLE"
    ON_RIDE = "ON_RIDE"


CAPTAIN_SHARE = 0.85  # captain keeps 85% of each completed fare


# ---------------------------------------------------------------------------
# Helper: Haversine great-circle distance
# ---------------------------------------------------------------------------
def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two coordinates, in kilometres."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Composition building blocks
# ---------------------------------------------------------------------------
class Location:
    """A named Quetta hub with coordinates."""

    def __init__(self, name, lat, lon):
        self._name = name
        self._lat = lat
        self._lon = lon

    @property
    def name(self):
        return self._name

    @property
    def latitude(self):
        return self._lat

    @property
    def longitude(self):
        return self._lon

    def distance_to(self, other):
        return haversine_km(self._lat, self._lon, other._lat, other._lon)


class Bike:
    """A motorbike belonging to a captain (has-a relationship)."""

    def __init__(self, model, plate, spare_helmet):
        self._model = model
        self._plate = plate
        self._spare_helmet = spare_helmet

    @property
    def model(self):
        return self._model

    @property
    def plate(self):
        return self._plate

    @property
    def spare_helmet(self):
        return self._spare_helmet

    def to_dict(self):
        return {"model": self._model, "plate": self._plate, "spare_helmet": self._spare_helmet}


# ---------------------------------------------------------------------------
# Abstraction + inheritance: User hierarchy
# ---------------------------------------------------------------------------
class User(ABC):
    """Shared attributes for every person using the platform."""

    def __init__(self, user_id, name, phone, rating=5.0):
        self._user_id = user_id
        self._name = name
        self._phone = phone
        self._rating = rating

    @property
    def user_id(self):
        return self._user_id

    @property
    def name(self):
        return self._name

    @property
    def rating(self):
        return self._rating

    @abstractmethod
    def get_role(self):
        """Every subclass MUST say what role it plays (enforced by ABC)."""

    def to_dict(self):
        return {
            "id": self._user_id,
            "name": self._name,
            "phone": self._phone,
            "rating": self._rating,
            "role": self.get_role(),
        }


class Rider(User):
    """A customer who books rides and pays from a PKR wallet."""

    def __init__(self, user_id, name, phone, balance=0.0):
        super().__init__(user_id, name, phone)
        self.__balance = 0.0  # private: only changed through topup()/charge()
        self.__student_verified = False
        if balance > 0:
            self.topup(balance)

    def get_role(self):
        return "Rider"

    @property
    def balance(self):
        return round(self.__balance, 2)

    @property
    def student_verified(self):
        return self.__student_verified

    def set_student_verified(self, verified):
        self.__student_verified = bool(verified)

    def has_funds(self, amount):
        return self.__balance >= amount

    def topup(self, amount):
        if amount <= 0:
            raise ValueError("Top-up amount must be positive.")
        self.__balance += amount

    def charge(self, amount):
        if amount > self.__balance:
            raise InsufficientBalanceError(
                f"Balance Rs {self.balance} is less than fare Rs {amount}."
            )
        self.__balance -= amount

    def to_dict(self):
        data = super().to_dict()
        data["balance"] = self.balance
        data["student_verified"] = self.student_verified
        return data


class Captain(User):
    """A bike captain. Composes a Bike and tracks live availability and earnings."""

    def __init__(self, user_id, name, phone, bike, rating=4.8):
        super().__init__(user_id, name, phone, rating)
        self._bike = bike                          # composition: Captain HAS-A Bike
        self.__status = CaptainStatus.AVAILABLE    # private: prevents double-booking
        self.__earnings = 0.0

    def get_role(self):
        return "Captain"

    @property
    def bike(self):
        return self._bike

    @property
    def status(self):
        return self.__status

    @property
    def is_available(self):
        return self.__status is CaptainStatus.AVAILABLE

    @property
    def earnings(self):
        return round(self.__earnings, 2)

    def take_ride(self):
        if not self.is_available:
            raise CaptainUnavailableError(f"{self._name} is already on a ride.")
        self.__status = CaptainStatus.ON_RIDE

    def release(self):
        self.__status = CaptainStatus.AVAILABLE

    def credit_fare(self, fare):
        self.__earnings += fare * CAPTAIN_SHARE

    def to_dict(self):
        data = super().to_dict()
        data.update(
            bike=self._bike.to_dict(),
            status=self.__status.value,
            earnings=self.earnings,
        )
        return data


# ---------------------------------------------------------------------------
# Abstraction + polymorphism: fare strategies
# ---------------------------------------------------------------------------
class FareStrategy(ABC):
    """Contract every pricing tier must follow."""

    name = "Base"

    @abstractmethod
    def calculate_fare(self, distance_km):
        """Return the fare in PKR for the given distance."""


class SaverFare(FareStrategy):
    name = "Sasta Saver"
    BASE = 50
    PER_KM = 25
    MINIMUM = 60

    def calculate_fare(self, distance_km):
        return max(self.MINIMUM, self.BASE + self.PER_KM * distance_km)


class ExpressFare(FareStrategy):
    name = "Sasta Express"
    BASE = 90
    PER_KM = 35

    def calculate_fare(self, distance_km):
        return self.BASE + self.PER_KM * distance_km


class ParcelFare(FareStrategy):
    name = "Bike Parcel"
    BASE = 120
    PER_KM = 30

    def calculate_fare(self, distance_km):
        return self.BASE + self.PER_KM * distance_km


# ---------------------------------------------------------------------------
# Ride: composes locations and a strategy; enforces the state machine
# ---------------------------------------------------------------------------
class Ride:
    _id_counter = itertools.count(1)  # class attribute shared by every Ride

    def __init__(
        self,
        rider,
        pickup,
        dropoff,
        strategy,
        payment_method,
        pickup_details="",
        dropoff_details="",
    ):
        self._ride_id = f"R{next(Ride._id_counter):03d}"
        self._rider = rider
        self._pickup = pickup        # composition: Ride HAS-A Location
        self._dropoff = dropoff
        self._pickup_details = pickup_details
        self._dropoff_details = dropoff_details
        self._strategy = strategy    # polymorphic pricing
        self._distance_km = round(pickup.distance_to(dropoff), 2)
        self._base_fare = round(strategy.calculate_fare(self._distance_km), 2)
        self._discount_percent = 30 if rider.student_verified else 0
        self._fare = round(self._base_fare * (1 - self._discount_percent / 100), 2)
        self._payment_method = payment_method
        self._captain = None
        self.__status = RideStatus.REQUESTED  # private: only moved via methods below

    @property
    def ride_id(self):
        return self._ride_id

    @property
    def status(self):
        return self.__status

    @property
    def fare(self):
        return self._fare

    @property
    def payment_method(self):
        return self._payment_method

    def _check_transition(self, allowed, target):
        if self.__status not in allowed:
            raise InvalidTransitionError(
                f"Ride {self._ride_id}: cannot go from {self.__status.value} to {target.value}."
            )

    def accept(self, captain):
        self._check_transition({RideStatus.REQUESTED}, RideStatus.ACCEPTED)
        captain.take_ride()          # raises if the captain is busy
        self._captain = captain
        self.__status = RideStatus.ACCEPTED

    def start(self):
        self._check_transition({RideStatus.ACCEPTED}, RideStatus.IN_PROGRESS)
        self.__status = RideStatus.IN_PROGRESS

    def complete(self):
        self._check_transition({RideStatus.IN_PROGRESS}, RideStatus.COMPLETED)
        if self._payment_method == "wallet":
            self._rider.charge(self._fare)       # wallet money moves only on completion
        self._captain.credit_fare(self._fare)
        self._captain.release()
        self.__status = RideStatus.COMPLETED

    def cancel(self):
        self._check_transition({RideStatus.REQUESTED, RideStatus.ACCEPTED}, RideStatus.CANCELLED)
        if self._captain is not None:
            self._captain.release()
        self.__status = RideStatus.CANCELLED

    def to_dict(self):
        return {
            "id": self._ride_id,
            "rider_id": self._rider.user_id,
            "rider": self._rider.name,
            "pickup": self._pickup.name,
            "pickup_details": self._pickup_details,
            "dropoff": self._dropoff.name,
            "dropoff_details": self._dropoff_details,
            "service": self._strategy.name,
            "distance_km": self._distance_km,
            "base_fare": self._base_fare,
            "discount_percent": self._discount_percent,
            "fare": self._fare,
            "payment_method": self._payment_method,
            "status": self.__status.value,
            "captain": self._captain.name if self._captain else None,
        }


# ---------------------------------------------------------------------------
# Aggregate: the whole platform
# ---------------------------------------------------------------------------
class SastaSawari:
    """Owns all hubs, users and rides, and exposes the operations the API needs."""

    def __init__(self):
        self._hubs = {}
        self._riders = {}
        self._captains = {}
        self._rides = {}
        self._student_applications = {}
        self._services = {s.name: s for s in (SaverFare(), ExpressFare(), ParcelFare())}

    # --- registration -----------------------------------------------------
    def add_hub(self, location):
        self._hubs[location.name] = location

    def add_rider(self, rider):
        self._riders[rider.user_id] = rider

    def add_captain(self, captain):
        self._captains[captain.user_id] = captain

    # --- helpers ----------------------------------------------------------
    @staticmethod
    def _find(store, key, label):
        if key not in store:
            raise SastaSawariError(f"Unknown {label}: {key}")
        return store[key]

    # --- use cases --------------------------------------------------------
    def topup(self, rider_id, amount):
        self._find(self._riders, rider_id, "rider").topup(amount)

    @staticmethod
    def _location_details(value, label):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{label} must be text.")
        details = value.strip()
        if len(details) > 200:
            raise ValueError(f"{label} must be 200 characters or fewer.")
        return details

    def submit_student_card(self, rider_id, filename, content_type, content):
        rider = self._find(self._riders, rider_id, "rider")
        if rider.student_verified:
            raise SastaSawariError("This rider already has verified student status.")
        application = self._student_applications.get(rider_id)
        if application and application["status"] == "PENDING":
            raise SastaSawariError("A student card application is already pending review.")
        self._student_applications[rider_id] = {
            "rider_id": rider_id,
            "rider_name": rider.name,
            "filename": filename,
            "content_type": content_type,
            "content": content,
            "status": "PENDING",
        }

    def pending_student_cards(self):
        return [
            {key: value for key, value in application.items() if key != "content"}
            for application in self._student_applications.values()
            if application["status"] == "PENDING"
        ]

    def student_card_file(self, rider_id):
        application = self._find(self._student_applications, rider_id, "student application")
        if application["status"] != "PENDING":
            raise SastaSawariError("This student card is no longer available for review.")
        return application

    def review_student_card(self, rider_id, approved):
        rider = self._find(self._riders, rider_id, "rider")
        application = self._find(self._student_applications, rider_id, "student application")
        if application["status"] != "PENDING":
            raise SastaSawariError("This student card has already been reviewed.")
        application["status"] = "APPROVED" if approved else "REJECTED"
        application["content"] = b""
        rider.set_student_verified(approved)

    def book_ride(
        self,
        rider_id,
        pickup_name,
        dropoff_name,
        service_name,
        payment_method="wallet",
        pickup_details="",
        dropoff_details="",
    ):
        rider = self._find(self._riders, rider_id, "rider")
        if any(
            ride.to_dict()["rider_id"] == rider_id
            and ride.status in {RideStatus.REQUESTED, RideStatus.ACCEPTED, RideStatus.IN_PROGRESS}
            for ride in self._rides.values()
        ):
            raise SastaSawariError(f"{rider.name} already has an active ride and is unavailable.")
        pickup = self._find(self._hubs, pickup_name, "hub")
        dropoff = self._find(self._hubs, dropoff_name, "hub")
        strategy = self._find(self._services, service_name, "service")
        if pickup is dropoff:
            raise SastaSawariError("Pickup and dropoff must be different hubs.")
        if payment_method not in {"wallet", "easypaisa", "jazzcash", "cash_at_location"}:
            raise SastaSawariError("Choose a supported payment method.")
        pickup_details = self._location_details(pickup_details, "Pickup address")
        dropoff_details = self._location_details(dropoff_details, "Drop-off address")

        ride = Ride(
            rider,
            pickup,
            dropoff,
            strategy,
            payment_method,
            pickup_details,
            dropoff_details,
        )
        if payment_method == "wallet" and not rider.has_funds(ride.fare):
            raise InsufficientBalanceError(
                f"Balance Rs {rider.balance} is less than fare Rs {ride.fare}. Please top up."
            )
        self._rides[ride.ride_id] = ride
        return ride

    def accept_ride(self, ride_id, captain_id=None):
        ride = self._find(self._rides, ride_id, "ride")
        if captain_id:
            captain = self._find(self._captains, captain_id, "captain")
        else:
            captain = next((c for c in self._captains.values() if c.is_available), None)
            if captain is None:
                raise CaptainUnavailableError("No captains are available right now.")
        ride.accept(captain)

    def start_ride(self, ride_id):
        self._find(self._rides, ride_id, "ride").start()

    def complete_ride(self, ride_id):
        self._find(self._rides, ride_id, "ride").complete()

    def cancel_ride(self, ride_id):
        self._find(self._rides, ride_id, "ride").cancel()

    def snapshot(self):
        """Everything the browser needs, as plain JSON-friendly data."""
        active_rider_ids = {
            ride.to_dict()["rider_id"]
            for ride in self._rides.values()
            if ride.status in {RideStatus.REQUESTED, RideStatus.ACCEPTED, RideStatus.IN_PROGRESS}
        }
        riders = []
        for rider in self._riders.values():
            data = rider.to_dict()
            data["available"] = rider.user_id not in active_rider_ids
            riders.append(data)
        return {
            "hubs": list(self._hubs.keys()),
            "hub_locations": [
                {"name": location.name, "lat": location.latitude, "lon": location.longitude}
                for location in self._hubs.values()
            ],
            "services": list(self._services.keys()),
            "riders": riders,
            "captains": [c.to_dict() for c in self._captains.values()],
            "rides": [r.to_dict() for r in reversed(list(self._rides.values()))],
        }


# ---------------------------------------------------------------------------
# Demo data for Quetta (coordinates are approximate and can be edited)
# ---------------------------------------------------------------------------
HUBS = [
    ("Jinnah Road", 30.1930, 67.0105),
    ("Zarghoon Road", 30.1795, 67.0013),
    ("Sariab Road", 30.1640, 67.0100),
    ("BUITEMS Takatu", 30.2431, 67.0640),
    ("Airport Road", 30.2513, 66.9378),
    ("Liaquat Bazar", 30.1885, 67.0005),
    ("Azat Ganji", 30.1190, 66.9660),
    ("Brewery Road", 30.1680, 67.0175),
    ("Satellite Town", 30.2070, 67.0220),
    ("Prince Road", 30.1860, 67.0100),
    ("Quetta Railway Station", 30.1937, 66.9984),
    ("University of Balochistan", 30.1990, 67.0400),
    ("Kandahari Bazar", 30.1880, 66.9980),
    ("Alamdar Road", 30.2020, 66.9820),
]


def build_demo_system():
    system = SastaSawari()
    for name, lat, lon in HUBS:
        system.add_hub(Location(name, lat, lon))
    system.add_rider(Rider("RD1", "Ahmed Khan", "0300-1234567", balance=500))
    system.add_rider(Rider("RD2", "Ayesha Baloch", "0301-2223344", balance=750))
    system.add_rider(Rider("RD3", "Hassan Shah", "0302-3334455", balance=350))
    system.add_rider(Rider("RD4", "Maryam Khan", "0303-4445566", balance=600))
    system.add_rider(Rider("RD5", "Usman Ahmed", "0304-5556677", balance=1000))
    system.add_captain(Captain("CP1", "Ali Raza", "0331-1112233",
                               Bike("Honda CD 70", "QTA-1234", True)))
    system.add_captain(Captain("CP2", "Bilal Khan", "0312-4445566",
                               Bike("Suzuki GD 110S", "QTA-5678", False)))
    system.add_captain(Captain("CP3", "Zain Ahmed", "0345-7778899",
                               Bike("Yamaha YBR 125", "QTA-9012", True)))
    return system
