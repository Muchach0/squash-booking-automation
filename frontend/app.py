from __future__ import annotations
from google.cloud import secretmanager

import os
import json

from flask import Flask, render_template, request

gcp_secret_path = "projects/1036179882263/secrets/squash_secrets" # project id + secret id
current_entries = {}
DEFAULT_MOMENT = "17:00-19:00"
def default_booking_windows() -> list[dict[str, str]]:
    moment = os.getenv("FRONTEND_DEFAULT_MOMENT", DEFAULT_MOMENT).strip() or DEFAULT_MOMENT
    if "-" in moment:
        start, end = [value.strip() for value in moment.split("-", 1)]
    else:
        start, end = "17:00", "19:00"

    return [
        {
            "day": "monday",
            "start": start,
            "end": end,
            "moment": moment,
        }
    ]


WEEKDAYS = [
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
]

app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    values = {
        "email": "",
        "password": "",
        "moment": os.getenv("FRONTEND_DEFAULT_MOMENT", DEFAULT_MOMENT),
        "booking_windows": default_booking_windows(),
    }
    delete_values = {
        "email": "",
    }
    errors: dict[str, str] = {}
    env_preview: dict[str, object] | None = None
    success_message: str | None = None

    if request.method == "POST":
        action = request.form.get("action", "upsert")

        if action == "delete":
            delete_values = {
                "email": request.form.get("delete_email", "").strip(),
            }

            if not delete_values["email"]:
                errors["delete_email"] = "Email is required."
        else:
            booking_windows, has_invalid_booking_window = parse_booking_windows(request.form)
            values = {
                "email": request.form.get("email", "").strip(),
                "password": request.form.get("password", ""),
                "booking_windows": booking_windows,
            }
            values["moment"] = values["booking_windows"][0]["moment"] if values["booking_windows"] else ""

            if not values["email"]:
                errors["email"] = "Email is required."
            if not values["password"]:
                errors["password"] = "Password is required."
            if not values["booking_windows"]:
                errors["booking_windows"] = "At least one booking window is required."
                values["booking_windows"] = default_booking_windows()
            elif has_invalid_booking_window:
                errors["booking_windows"] = "Please complete each booking window with a day and a valid time range."

        if not errors:
            try:
                current_entries = json.loads(read_secret())
            except Exception as e:
                app.logger.exception("Unable to load booking entries from Secret Manager.")
                errors["form"] = (
                    "Could not load the current bookings from Secret Manager. "
                    f"Details: {e}"
                )


        if not errors and action == "delete":
            try:
                delete_entry(current_entries, delete_values["email"])
            except Exception as e:
                app.logger.exception("Unable to prepare booking entry deletion.")
                errors["form"] = (
                    "Could not prepare the booking deletion. "
                    f"Details: {e}"
                )
        elif not errors:
            # Checks to see if the user is already present if yes, we should update the entry instead.
            try:
                update_entry(current_entries, values)
            except Exception as e:
                app.logger.exception("Unable to prepare booking entry update.")
                errors["form"] = (
                    "Could not prepare the booking registration. "
                    f"Details: {e}"
                )



        if not errors:
            # writing secret to GCP
            try:
                write_secret(json.dumps(current_entries))            
            except Exception as e:
                app.logger.exception("Unable to write booking entries to Secret Manager.")
                errors["form"] = (
                    "Could not save the booking changes to Secret Manager. "
                    f"Details: {e}"
                )

            if errors:
                success_message = None
            elif action == "delete":
                success_message = "Entry deleted successfully."
            else:
                success_message = "Registration saved successfully."
                env_preview = {
                    "RESAMANIA_EMAIL": values["email"],
                    "RESAMANIA_PASSWORD": values["password"],
                    "RESAMANIA_MOMENT": values["moment"],
                    "BOOKING_WINDOWS": values["booking_windows"],
                }
            

    return render_template(
        "index.html",
        values=values,
        delete_values=delete_values,
        errors=errors,
        env_preview=env_preview,
        success_message=success_message,
        weekdays=WEEKDAYS,
    )


def parse_booking_windows(form) -> tuple[list[dict[str, str]], bool]:
    windows = []
    has_invalid_window = False
    valid_days = {value for value, _ in WEEKDAYS}
    days = form.getlist("booking_day")
    starts = form.getlist("booking_start")
    ends = form.getlist("booking_end")

    for day, start, end in zip(days, starts, ends):
        day = day.strip().lower()
        start = start.strip()
        end = end.strip()

        if not day and not start and not end:
            continue
        if day not in valid_days or not start or not end or start >= end:
            has_invalid_window = True
            continue

        windows.append({
            "day": day,
            "start": start,
            "end": end,
            "moment": f"{start}-{end}",
        })

    return windows, has_invalid_window


def update_entry(current_entries: dict, new_value: dict):
    if "users" not in current_entries: # if we init the dict
        current_entries["users"] = []
    for i, user in enumerate(current_entries["users"]): # go through each user, and update entry if present
        if user["email"] == new_value["email"]:
            current_entries["users"][i] = new_value
            return True
    # user not found, adding a new entry
    current_entries["users"].append(new_value)
    return True


def delete_entry(current_entries: dict, email: str):
    users = current_entries.get("users", [])
    current_entries["users"] = [
        user for user in users
        if user.get("email") != email
    ]
    return True


def write_secret(secret_value: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    response = client.add_secret_version(
        request={
            "parent": gcp_secret_path,
            "payload": {
                "data": secret_value.encode("UTF-8")
            },
        }
    )
    return response.name

def read_secret() -> str:
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={
        "name": gcp_secret_path + "/versions/latest"
        })
    return response.payload.data.decode("UTF-8")


# @app.route("/secret", methods=["GET"])
# def show_secret():
#     return read_secret()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
