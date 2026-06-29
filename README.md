# Standards Agent (Project Data Pull)

A FastAPI-based web application for validating Salesforce real estate project data against Airtable standards. This tool extracts furniture, floor, and space data from Autodesk BIM 360/ACC Revit models and validates them against the official Salesforce furniture and space type standards.

## Overview

Standards Agent provides:
- **Furniture Schedule Validation**: Compare furniture in Revit models against Airtable standards
- **Capacity Calculations**: Calculate IW, Open Collab, and Amenity seat counts
- **Floor Schedules**: Extract floor finish data with area calculations
- **Benchmark Reports**: Compare floor area distributions across multiple projects
- **Standards Audit**: Check if the Global Standards Revit file matches Airtable active standards
- **Scheduled Reporting**: Automated report generation with Google Drive integration

## Features

### 1. Furniture Schedule
- Extracts furniture families from Revit models via Autodesk Platform Services (APS) API
- Validates against Airtable standards (Frame Tag, Manufacturer, Region)
- Identifies missing, retired, or non-standard furniture
- Supports itemized view showing every furniture instance with location
- Detects cross-region furniture usage

### 2. Capacity Engine
- Spatial join between furniture model and architecture model
- Assigns furniture to rooms and calculates seat counts
- Groups by space type: IW (Individual Workspace), Open Collab, Amenity
- Level-by-level breakdown with totals

### 3. Floor Schedule
- Extracts floor types with area calculations
- Groups by Type Mark (e.g., CP-01, RB-01)
- Aggregates area per level
- Extracts Type Mark from Type name if parameter is empty

### 4. Benchmark Analysis
- Compare floor area distributions across multiple projects
- Calculate % distribution by floor type prefix (CP, RB, TL, etc.)
- Identify outliers and consistency across portfolio
- Parallel processing for fast multi-project analysis

### 5. Standards Audit
- Compare Global Standards Revit file against Airtable active standards
- Identify missing standards (in Airtable but not Revit)
- Flag retired items still in Revit
- List non-standard items (in Revit but not Airtable)

### 6. Scheduled Reports
- Configure recurring capacity and furniture reports
- Automatic Google Drive upload
- Configurable intervals (daily, weekly, bi-weekly, monthly)
- Email notification support (via scheduler)

## Installation

### Prerequisites
- Python 3.9+
- Autodesk Platform Services (APS) credentials
- Airtable API key
- Google Workspace credentials (optional, for Drive integration)

### Setup

1. Clone the repository:
```bash
git clone https://github.com/juliahanlon-sudo/standards-agent.git
cd standards-agent
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file with your credentials:
```bash
APS_CLIENT_ID=your_aps_client_id
APS_CLIENT_SECRET=your_aps_client_secret
AIRTABLE_API_KEY=your_airtable_api_key
AIRTABLE_BASE_ID=appW5LiBnNMb9Pkid
```

4. Run the application:
```bash
uvicorn main:app --reload
```

The application will start at `http://localhost:8000`

## Usage

### Web Interface

Open `http://localhost:8000` in your browser. The interface provides:

1. **Project Selection**: Browse BIM 360/ACC projects
2. **Model Selection**: Choose architecture and furniture models
3. **Report Type**: Select schedule type (furniture, capacity, floors, doors, etc.)
4. **Validation**: View validation results with color-coded status

### API Endpoints

#### List Projects
```bash
GET /api/projects
```

#### Get Models in Project
```bash
GET /api/projects/{project_id}/models?hub_id={hub_id}
```

#### Get Furniture Schedule
```bash
GET /api/schedule?urn={model_urn}&schedule_type=furniture&project_name={project_name}
```

#### Calculate Capacity
```bash
GET /api/capacity?furniture_urn={furniture_urn}&interior_urn={interior_urn}
```

#### Run Benchmark
```bash
GET /api/benchmark?project_ids={id1,id2,id3}&project_names={name1,name2,name3}
```

#### Standards Audit
```bash
GET /api/standards-audit?urn={global_standards_urn}
```

## Schedule Types

The tool supports multiple schedule types:

- **furniture**: Furniture schedule with Airtable validation
- **rooms**: Room schedule with occupancy data
- **floors**: Floor finishes with area calculations
- **doors**: Door schedule with hardware details
- **casework**: Casework schedule
- **finishes**: Room finishes (floor, wall, base, ceiling)
- **areas**: Area schedules by scheme and level

## Validation Status

Furniture validation returns color-coded statuses:

- 🟢 **Green**: Valid - Frame Tag matches Airtable, correct building/region
- 🟡 **Yellow**: Warning - Tag found but wrong region
- 🔴 **Red**: Error - Tag not found in Airtable or retired
- ⚪ **Gray**: No tag - SFDC_Tag Number or Type Mark parameter is empty

## Architecture

### Core Modules

- **main.py**: FastAPI application with all endpoints
- **aps_client.py**: Autodesk Platform Services API client
- **airtable_client.py**: Airtable API client with validation logic
- **capacity_engine.py**: Seat count calculation engine
- **spatial_join.py**: 3D spatial join for furniture-to-room assignment
- **benchmark_engine.py**: Multi-project comparison engine
- **report_runner.py**: Scheduled report generation with APScheduler
- **auth.py**: OAuth token management for APS

### Data Flow

1. **Model Discovery**: List projects and models via APS API
2. **Data Extraction**: Parse Revit model hierarchy and properties
3. **Validation**: Compare against Airtable standards
4. **Aggregation**: Group by type, calculate totals
5. **Presentation**: Return structured JSON to frontend

### Spatial Join

For capacity calculations, the tool performs a 3D spatial join:
1. Extract furniture instances with SFDC_Seat Count from furniture model
2. Extract room boundaries from architecture model
3. Calculate furniture centroid from bounding box
4. Assign furniture to room if centroid is within room boundary (point-in-polygon test)
5. Sum seat counts by room and space type

## Configuration

### Airtable Base Structure

Expected tables:
- **Space Types**: Architecture Room Name, Floor Type, Room Category
- **Furniture**: Frame Tag, Family Name, Type Name, Manufacturer, Status, Building Code
- **Buildings**: GCal Name, Building Code (SV), Region, Shipping Address
- **Manufacturers**: Manufacturer Name, Abbreviation

### Default Hub ID

The application defaults to Salesforce BIM 360 hub:
```python
HUB_ID = "b.8a643169-4b2b-4c79-bff4-289208a76b2e"
```

Override via query parameter: `?hub_id={your_hub_id}`

## Scheduled Reports

Configure recurring reports via the UI or API:

```json
{
  "name": "Weekly Capacity Report",
  "enabled": true,
  "models": [
    {"project_id": "...", "furniture_urn": "...", "interior_urn": "..."}
  ],
  "report_types": ["capacity", "furniture"],
  "interval_days": 7,
  "drive_folder_id": "1A2B3C4D5E",
  "hub_id": "b.8a643169-4b2b-4c79-bff4-289208a76b2e"
}
```

## Development

### Running Tests

```bash
# Test SVF geometry parsing
python test_svf_geometry.py

# Test architecture model detection
python test_arch_model.py

# Test SVF parser
python test_svf_parser.py
```

### Debug Mode

Set debug flags in code:
```python
# In spatial_join.py
DEBUG = True  # Print detailed spatial join logs
```

## Troubleshooting

**No furniture found in model**:
- Ensure the model has Furniture or Furniture Systems category
- Try multiple 3D views (master view may be filtered)
- Check that furniture has properties (not just geometry)

**Validation showing all gray**:
- Check that SFDC_Tag Number or Type Mark parameter is populated in Revit
- Verify Airtable API key is valid
- Confirm Frame Tag exists in Airtable

**Capacity calculation returns 0**:
- Verify furniture has SFDC_Seat Count parameter populated
- Check that both furniture and architecture models are provided
- Ensure rooms have closed boundaries (no gaps)

**Slow performance**:
- The first request fetches and caches model data
- Parallel processing is used for multi-project benchmarks
- Consider caching frequently accessed models

## Repository

GitHub: [juliahanlon-sudo/standards-agent](https://github.com/juliahanlon-sudo/standards-agent)

## License

Internal Salesforce tool

## Contributors

Developed for Salesforce Real Estate & Workplace Services
