# Fuel Route API

A Django REST API that builds a driving route between two U.S. locations, chooses fuel stops from the included fuel-price CSV, and estimates fuel cost for a vehicle with a 500-mile range and 10 MPG.

## Requirements

- Python 3.14
- A FreeRoute API key

## Setup (Windows PowerShell)

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\encrypt_key.py
```

Enter the FreeRoute key at the hidden prompt. The encrypted value is written to `.env.key`; the local Fernet key is stored under `%LOCALAPPDATA%\fuel_map_api\key.key`. Neither key belongs in GitHub. Alternatively, set `FREEROUTE_API_KEY` in the process environment.

Start the development server from the repository root:

```powershell
python .\manage.py runserver
```

## Request

`POST http://127.0.0.1:8000/api/map/`

Content-Type: `application/json`

```json
{
  "start": "Fort Wayne, IN",
  "finish": "Seymour, IN"
}
```

The response includes route distance, route geometry, recommended fuel stops, and estimated total fuel cost.

## Tests

```powershell
python .\manage.py test
python .\manage.py check
```

## Fuel-stop limitation

The CSV contains station city/state and prices, but no station coordinates. The API therefore selects the lowest-priced available station in the state at each refueling point; it cannot verify that a station is near the route or calculate a real detour distance.
