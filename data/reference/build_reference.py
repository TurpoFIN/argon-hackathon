"""Builds data/reference/unit_costs.json and damage_functions.json from the archived sources.

Run: .venv/bin/python -I data/reference/build_reference.py
Inputs (all archived in this directory): hazus_fast/*.csv (FEMA Hazus FAST lookup tables),
egm09-04.pdf (USACE vehicle curves), unit_costs_research*.json (web research log with quotes).
Every item carries source + url + retrieved date. 'sf_factor' = location factor applied to
non-Bay-Area construction prices (RSMeans-type SF city cost index ~1.30).
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RET = "2026-10-25"
FT = 0.3048
SF_FACTOR = 1.30
R1 = json.load(open(os.path.join(HERE, "unit_costs_research.json")))["items"]
R2 = json.load(open(os.path.join(HERE, "unit_costs_research2.json")))["items"]


def src(key, store=None):
    s = (store or R1 if key in R1 else R2)[key]
    return {"source": s.get("source", ""), "url": s.get("url", ""), "quote": (s.get("quote") or "")[:400],
            "source_year": s.get("year")}


def item(usd, unit, desc, key=None, sf=False, derived=None, extra_src=None, conf="medium", **kw):
    d = {"usd": round(usd * (SF_FACTOR if sf else 1.0), 2), "unit": unit, "description": desc,
         "sf_factor": SF_FACTOR if sf else 1.0, "retrieved": RET, "confidence": conf}
    if key:
        d.update(src(key))
    if extra_src:
        d.update(extra_src)
    if derived:
        d["derived"] = derived
    d.update(kw)
    return d


SF_MULT_SRC = src("sf_construction_cost_multiplier")
BURDEN = 1 / 0.615  # state & local gov wages = 61.5% of compensation (BLS ECEC June 2025)
ECEC = {"source": "BLS Employer Costs for Employee Compensation, June 2025 (state & local government: wages 61.5% of compensation)",
        "url": "https://www.bls.gov/news.release/archives/ecec_09122025.htm", "source_year": 2025}

# ------------------------------------------------------------------ unit costs
U = {}
# earthworks / paving
U["earthworks_m3"] = item(128.0, "m3", "Cut/fill/regrade of street or ground (volume of |dz| x area)",
                          "earthworks_roadway_excavation_ca", sf=False, conf="medium",
                          derived="Windsor CA 2024 roadway excavation $97.85/yd3 = $128/m3 (Bay Area, no SF factor)")
U["repave_m2"] = item(62.8, "m2", "Re-surfacing of regraded paved area (cold plane + 100 mm HMA)", "repaving_asphalt",
                      derived="Contra Costa 2024 HMA $242.5/t x 0.1 m x 2.4 t/m3 + cold plane $25.75/yd2")
U["floodwall_low_m_per_m"] = item(403.0, "m length per m height (h<=1.2 m)",
                                  "Low permanent concrete/masonry floodwall (<=1.2 m)", "floodwall_lsu_residential_4ft", sf=True,
                                  derived="LSU AgCenter $150/lf at ~4 ft (1.22 m) -> $492/m / 1.22 m = $403 per m per m height")
U["floodwall_high_m_per_m"] = item(5739.0, "m length per m height (h>=2.5 m)",
                                   "Engineered floodwall (pile-founded, USACE NACCS parametric)", "floodwall_usace_naccs_10ft", sf=True,
                                   derived="$5,335/lf for 10 ft -> $17,503/m / 3.05 m = $5,739 per m per m height")
U["deployable_barrier_m"] = item(2215.0, "m length (~1.2 m tall)", "Deployable/temporary flood barrier (AquaFence-type)",
                                 "deployable_flood_barrier", conf="low")
U["flipup_barrier_m2"] = item(10000.0, "m2 barrier face", "Passive flip-up / self-closing flood gate", "passive_flipup_flood_barrier",
                              conf="medium")
U["permeable_pavement_m2"] = item(86.1, "m2", "Permeable pavement retrofit (pervious concrete/asphalt/pavers)",
                                  "permeable_pavement_epa", sf=True, design_ksat_mm_h=12.7,
                                  design_ksat_note="EPA permeable pavement fact sheet: subgrade infiltration >= 0.5 in/h (12.7 mm/h) required; used as the effective surface ksat")
U["bioswale_m2"] = item(269.1, "m2", "Bioretention / bioswale green street (Bay Area planning cost)",
                        "green_street_bioretention_san_mateo")
U["green_roof_m2"] = item(239.5, "m2 roof", "Extensive green roof retrofit", "green_roof_epa", sf=True)
U["storage_m3"] = item(1123.0, "m3 storage", "Underground concrete cistern / storage vault, installed", "underground_cistern_storage",
                       sf=True, derived="$4.25/gal installed x 264.17 gal/m3")
U["pump_station_m3s"] = item(1377274.0, "m3/s capacity", "Permanent stormwater pump station", "stormwater_pump_station_alexandria",
                             sf=True, derived="$39,000/cfs x 35.315 cfs per m3/s")
U["tunnel_m_ref"] = item(218176.0, "m length at D=9.9 m (scaled linearly with diameter, min D 3 m)",
                         "Stormwater conveyance tunnel (TBM)", "large_stormwater_tunnel_hcfcd", sf=True,
                         derived="$66,500/lf for 25-40 ft diameter; D for capacity Q from Q = 3 m/s x pi D^2/4 (full-flow design velocity, ASCE MOP 77 range 0.9-4.5 m/s)",
                         d_ref_m=9.9, v_design_ms=3.0, d_min_m=3.0)
U["inlet_new"] = item(6695.0, "each", "New curb-opening catch basin / storm inlet incl. lateral", "catch_basin_inlet_ca")
U["inlet_upgrade"] = item(6695.0, "each", "Upgrade (rebuild/enlarge) existing inlet: priced as a new CA catch basin",
                          "catch_basin_inlet_ca", conf="low")
U["dry_floodproof_m2"] = item(1001.0, "m2 building footprint (<=0.9 m)", "Dry floodproofing: sealants, closures, backflow valves",
                              "dry_floodproofing_usace_nyc_hats", conf="medium", max_height_m=0.9,
                              note="FEMA P-936: dry floodproofing generally limited to ~3 ft (0.9 m)")
U["flood_shield_opening"] = item(8250.0, "door/opening", "Flood shield or flood door per opening", "flood_shield_door_barrier")
U["wet_floodproof_m2"] = item(80.7, "m2 footprint", "Wet floodproofing (abandon lower level, raise utilities, flood vents)",
                              "fema_p312_abandon_lower_level", sf=True)
U["tree_prune"] = item(219.95, "tree", "Structural/crown-reduction pruning", "tree_contract_prices_riverside_ca", sf=True)
U["tree_remove_per_cm_dbh"] = item(45.55 / 2.54, "cm DBH", "Tree removal incl. stump (contract rate per inch DBH)",
                                   "tree_contract_prices_riverside_ca", sf=True)
U["tree_removal_permit"] = item(499.0, "permit (1-3 trees)", "SF Public Works street tree removal permit", "sf_tree_removal_permit_fee")
U["tree_replace"] = item(2590.0, "tree", "Replacement street tree (SF in-lieu planting fee = city's cost to plant & establish)",
                         "sf_tree_in_lieu_replacement_fee")
U["tree_brace"] = item(1150.0, "tree", "Cabling + bracing of a street tree (midpoint $800-1,500)",
                       extra_src={"source": "Atlanta Arbor - Tree Bracing and Cabling cost ($800-1,500 combined per tree)",
                                  "url": "https://www.atlantaarbor.com/blog/bracing-trees/", "source_year": 2025},
                       sf=True, conf="low")
U["wind_screen_m2"] = item(89.6, "m2 screen face", "Porous (50%) wind fence, installed",
                           extra_src={"source": "Windbreak fencing installed $35-65/lf (fence contractor, Concord NC)",
                                      "url": "https://fencecompanyconcordnc.com/commercial/windbreak-fencing/", "source_year": 2023},
                           sf=True, conf="low", derived="$50/lf midpoint / (0.3048 m x 1.83 m typical height)")
U["sandbag"] = item(2.78, "filled+placed bag", "Sandbag filled and placed (materials, equipment, labour)", "sandbag_wall_cost",
                    derived="$125/lf for a 3 ft dike / 45 bags per lf (USACE pyramid N=(3H+9H^2)/2, H ft)", conf="low")
# emergency operations (FEMA Schedule of Equipment Rates 2025: equipment only, operator extra)
U["labour_crew_hr"] = item(43.66 * BURDEN, "worker-hour", "Public works crew labour, fully burdened",
                           "bls_oews_sf_construction_extraction_mean_hourly_2025",
                           derived=f"BLS OEWS SF $43.66/h / 0.615 (ECEC wage share) = ${43.66 * BURDEN:.2f}")
U["labour_tree_hr"] = item(25.46 * BURDEN, "worker-hour", "Tree/grounds crew labour, fully burdened",
                           "bls_oews_sf_grounds_maintenance_group_mean_hourly_2025", derived="BLS wage / 0.615")
U["labour_driver_hr"] = item(30.45 * BURDEN, "worker-hour", "Truck driver labour, fully burdened",
                             "bls_oews_sf_transport_material_moving_mean_hourly_2025", derived="BLS wage / 0.615")
for k, key in [("truck_flatbed_hr", "fema_truck_flatbed_25kgvw_dsl_hr"), ("truck_dump_hr", "fema_truck_dump_12cy_hr"),
               ("truck_pickup_hr", "fema_truck_pickup_3_4t_crew_hr"), ("tow_truck_hr", "fema_truck_tow_rollback_hr"),
               ("vactor_truck_hr", "fema_vactor_combo_vacuum_truck_hr"), ("sewer_jetter_hr", "fema_sewer_jetter_25gpm_hr"),
               ("bucket_truck_hr", "fema_aerial_lift_truck_55ft_hr"), ("chipper_hr", "fema_chipper_brush_bc1000xl_hr"),
               ("loader_hr", "fema_loader_wheel_cat926_hr"), ("light_tower_hr", "fema_light_tower_8kw_led_hr"),
               ("generator_100kw_hr", "fema_generator_100kw_hr"), ("generator_500kw_hr", "fema_generator_500kw_hr"),
               ("water_truck_hr", "fema_truck_water_2500gal_hr"), ("sweeper_hr", "fema_sweeper_pavement_elgin_pelican_hr")]:
    U[k] = item(R2[key]["usd"], "hour (equipment, excl. operator)", R2[key].get("spec", ""), key)
U["portable_pump_6in_hr"] = item(56.67, "hour (0.0625 m3/s = 990 gpm)", "6 in diesel trash pump, trailer mounted",
                                 "fema_pump_trash_6in_diesel_hr", capacity_m3s=0.0625)
U["portable_pump_12in_hr"] = item(34.82, "hour (0.368 m3/s = 5,833 gpm)", "12 in diesel centrifugal pump (hoses extra)",
                                  "fema_pump_centrifugal_12in_hr", capacity_m3s=0.368)
U["drain_clear_inlet"] = item(95.0, "inlet", "Pre-storm catch-basin/inlet cleaning (vactor), municipal contract unit price",
                              "catch_basin_cleaning_scarsdale_ny", low=40.94, high=300.0)
U["tow_car"] = item(652.0, "car", "Tow a parked car off a flood street (SFMTA tow + admin fee as price proxy)",
                    "sfmta_tow_total_first_time_same_day")
U["shelter_room_night"] = item(50.0, "room-night", "Non-congregate shelter (hotel room) per night, FEMA-accepted",
                               "fema_noncongregate_shelter_room_night_nc", conf="low")
U["_meta"] = {"sf_factor": SF_FACTOR, "sf_factor_source": SF_MULT_SRC, "burden_source": ECEC, "retrieved": RET,
              "currency": "nominal USD of the source year (not escalated)",
              "context": {k: src(k) | {"usd": R1[k].get("usd")} for k in
                          ("sfpuc_gi_grant_cap_per_impervious_acre", "fasip_tunnel_geometry", "fasip_phase2_srf_loan",
                           "sfpuc_flood_resilience_3_projects", "wawona_stormwater_project", "floodwall_nyc_escr_allin",
                           "discount_rate_fema_bca", "fema_bca_useful_life")}}
json.dump(U, open(os.path.join(HERE, "unit_costs.json"), "w"), indent=1)

# ------------------------------------------------------------------ damage functions
HZ = os.path.join(HERE, "hazus_fast")
FAST = {"source": "FEMA Hazus FAST (Flood Assessment Structure Tool) lookup tables, Hazus 4.0 default riverine DDFs",
        "url": "https://github.com/nhrap-hazus/FAST/tree/main/Lookuptables", "retrieved": RET}
cols = ["m4", "m3", "m2", "m1"] + [f"p{i}" for i in range(25)]
depth_ft = [-4, -3, -2, -1] + list(range(25))


def ddf(fname):
    out = {}
    for r in csv.DictReader(open(os.path.join(HZ, fname), encoding="utf-8-sig")):
        if r["Basement"] == "1":
            continue
        occ = r["Occupancy"]
        key = occ
        if occ == "RES1":
            key = {"1 Story": "RES1_1", "2 Story": "RES1_2", "3 Story": "RES1_3"}.get(r["Stories"])
            if key is None:
                continue
        if occ.startswith("RES3"):
            key = "RES3"
        if key in out:
            continue
        out[key] = {"ddf_id": int(r["DDF_ID"]), "src": r["Source"], "desc": r["Description"].strip(),
                    "pct": [float(r[c]) for c in cols]}
    return out


flood = {"depth_m": [round(d * FT, 4) for d in depth_ft], "depth_ft": depth_ft,
         "depth_reference": "water depth above first finished floor (Hazus convention)",
         "facade_depth_quantile": 0.5,
         "facade_depth_note": "depth at a building = median water depth over the 4 m cells touching its footprint (robust to a single "
                              "deep gutter cell); Hazus applies one flood depth per structure",
         "storey_exposure": {
             "rule": "exposed value share = affected storeys / total storeys, affected = max(1, ceil(depth above first floor / storey height)); "
                     "storey height = lidar building height / floors (min 2.5 m); applied to structure, contents and BI/relocation floor area. "
                     "The Hazus damage % itself is unchanged.",
             "why": "Documented departure from Hazus for multi-storey buildings: Hazus is a regional (census-block) method whose default "
                    "curves assume low-rise stock and apply one whole-building % at any height (Hazus 5.1 Flood TM Table 5-2), which "
                    "charges flood damage to dry upper storeys of SF mid/high-rises. USACE NACCS computes damage per affected storey.",
             "source": "USACE (2015) North Atlantic Coast Comprehensive Study: Physical Depth-Damage Function Summary Report, USACE North Atlantic Division",
             "url": "https://www.nad.usace.army.mil/Portals/40/docs/NACCS/10A_PhysicalDepthDmgFxSummary_26Jan2015.pdf",
             "hazus_reference": "https://www.fema.gov/sites/default/files/documents/fema_hazus-flood-model-technical-manual-5-1.pdf",
             "retrieved": RET},
         "first_floor_height": {
             "by_foundation_m": {"slab": 0.3048, "crawlspace": 0.9144, "basement": 1.2192, "fill": 0.6096,
                                 "pier": 1.524, "pile": 2.1336, "solid_wall": 2.1336},
             "by_foundation_ft": {"slab": 1, "crawlspace": 3, "basement": 4, "fill": 2, "pier": 5, "pile": 7, "solid_wall": 7},
             "default_foundation": "slab",
             "default_note": "SF inventory has no foundation type; Hazus non-basement default (slab-on-grade, 1 ft) used for all buildings. "
                             "Hazus '1st floor' = lowest floor (ground-floor garages/retail count).",
             "source": "FEMA Hazus Inventory Technical Manual 6.1 (Aug 2024), default first-floor heights above grade by foundation type; "
                       "values as tabulated in Nastev & Todorov 2013, Canadian Water Resources Journal 38(3) (pile 7, pier 5, solid wall 7, "
                       "basement 4, crawlspace 3, fill 2, slab 1 ft)",
             "url": "https://www.fema.gov/sites/default/files/documents/fema_hazus-inventory-technical-manual-6.1.pdf",
             "url2": "https://www.tandfonline.com/doi/full/10.1080/07011784.2013.801599", "retrieved": RET},
         "contents_to_structure": "buildings.json value_contents = Hazus contents ratio x structure value (RES 50%, COM1-5/GOV1/EDU1 100%, "
                                  "COM6-7/IND/GOV2/EDU2 150%, COM10 50%; Hazus Inventory TM contents value ratios), set by sim/prep.py meta.contents_ratio",
         "structure": ddf("Building_DDF_Riverine_LUT_Hazus4p0.csv"), "contents": ddf("Content_DDF_Riverine_LUT_Hazus4p0.csv"),
         **FAST}
rest = {}
for r in csv.DictReader(open(os.path.join(HZ, "flRsFnGBS_LUT.csv"), encoding="utf-8-sig")):
    rest.setdefault(r["Occupancy"], []).append([float(r["Min_Depth"]), float(r["Max_Depth"]),
                                                float(r["Min_Restor_Days"]), float(r["Max_Restor_Days"])])
econ = {}
for fn, fields in [("flBldgEconParamWageCapitalIncome.csv", ["IncomePerSqftPerDay", "WagePerSqftDay", "OutputPerSqftDay"]),
                   ("flBldgEconParamRental.csv", ["RentalCostsPerSqFtPerDay", "DisruptionCostsPerSqFt"]),
                   ("flBldgEconParamRecaptureFactors.csv", ["PctIncomeRecapture", "PctWageRecapture"]),
                   ("flBldgEconParamOwnerOccupied.csv", ["PctOwnerOccupied"])]:
    for r in csv.DictReader(open(os.path.join(HZ, fn), encoding="utf-8-sig")):
        o = "RES3" if r["Occupancy"].startswith("RES3") else r["Occupancy"]
        econ.setdefault(o, {}).update({f: float(r[f]) for f in fields})
EGM = {"source": "USACE EGM 09-04 Generic Depth-Damage Relationships for Vehicles (22 Jun 2009), Table Three",
       "url": "https://planning.erdc.dren.mil/toolbox/library/EGMs/egm09-04.pdf", "retrieved": RET,
       "archived": "data/reference/egm09-04.pdf"}
veh_ft = [0, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
vehicles = {"depth_ft_above_ground": veh_ft, "depth_m": [round(d * FT, 4) for d in veh_ft],
            "sedan": [0, 7.6, 28.0, 46.2, 62.2, 76.0, 87.6, 97.0, 100, 100, 100, 100],
            "pickup": [0, 5.2, 20.3, 34.4, 47.5, 59.6, 70.7, 80.8, 89.9, 98.0, 100, 100],
            "suv": [0, 0.0, 13.8, 30.6, 45.8, 59.4, 71.4, 81.8, 90.6, 97.8, 100, 100],
            "min_damage_depth_m": 0.1524, "min_damage_note": "no damage below the first tabulated depth (0.5 ft) of EGM 09-04",
            "fleet_mix_used": "sedan (57% of surveyed vehicles, EGM 09-04 Table Two)", **EGM}
D = {
    "flood": flood,
    "restoration_days": {"rows": rest, "columns": ["min_depth_ft", "max_depth_ft", "min_days", "max_days"],
                         "use": "min_days (Hazus lower bound) to stay conservative", **FAST},
    "economic": {"by_occupancy": econ, "units": "USD per sq ft (Hazus defaults)", **FAST,
                 "method": "Hazus Flood Technical Manual ch. 14 direct economic loss: income loss = FA x income/day x LOF days x (1 - recapture); "
                           "rental/relocation = FA x rent/day x days x (1 - owner%) + FA x disruption x owner%"},
    "vehicles": vehicles,
    "glazing": {
        "design_pressure_pa": {"pre1980": 958.0, "res": 1436.0, "com": 1915.0},
        "design_pressure_source": "AAMA/WDMA/CSA 101/I.S.2/A440 performance grades DP20/DP30/DP40 (psf) = 958/1436/1915 Pa; "
                                  "structural test pressure = 1.5 x DP without breakage",
        "url": "https://www.fgiaonline.org/pages/aama-wdma-csa-101-i-s-2-a440",
        "median_capacity_factor": 2.0, "beta": 0.25,
        "capacity_note": "median breakage at 2 x DP (above the 1.5 x DP proof test; ASTM E1300 designs for 8/1000 breakage at design load). Assumption flagged.",
        "window_to_wall": {"res": 0.25, "com": 0.40,
                           "source": "ASHRAE 90.1 Appendix G baseline window-to-wall ratio 40% (commercial); residential 25% assumption"},
        "storefront_usd_m2": U and src("storefront_glazing_bay_area") | {"usd_m2": 1238.0},
        "curtain_wall_usd_m2": src("curtain_wall_glazing") | {"usd_m2": 3229.0},
        "curtain_wall_min_height_m": 25.0,
        "facade_fraction_exposed": 0.25, "facade_fraction_note": "one facade (windward/peak suction face) of four",
        "retrieved": RET},
    "trees": {
        "car_total_loss_dbh_cm": 30.0,
        "car_total_loss_note": "trunk of DBH >= 30 cm crushing a car = total loss (car value from cars.json); smaller stems/branches = partial claim",
        "car_partial_claim_usd": 4000.0, "car_partial_source": src("tree_on_property_claim_avg"),
        "building_strike_usd": 14747.0, "building_strike_source": src("wind_hail_homeowner_claim_avg"),
        "cleanup": "failed tree: removal (tree_remove_per_cm_dbh x DBH) + replacement (tree_replace) from unit_costs.json",
        "strike_radius_m": 1.5, "retrieved": RET},
    "people": {
        "persons_per_household": 2.26,
        "pph_source": "US Census QuickFacts San Francisco County, persons per household 2019-2023 (ACS)",
        "pph_url": "https://www.census.gov/quickfacts/sanfranciscocountycalifornia",
        "unit_floor_area_m2": 85.0,
        "unit_area_note": "gross residential floor area per dwelling unit; SF average apartment ~ 750-900 sq ft (assumption, flagged)",
        "affected_rule": "residents of buildings with water above first floor, or with >= 0.3 m of water at the door (access cut)",
        "access_cut_depth_m": 0.3,
        "access_cut_source": "FEMA/USGS: 12 in of moving water can float a car, 6 in knocks an adult down (NWS Turn Around Don't Drown)",
        "access_cut_url": "https://www.weather.gov/safety/flood-turn-around-dont-drown",
        "vsl_usd": 13600000.0, "vsl_source": src("hhs_vsl_2024usd"),
        "retrieved": RET},
}
json.dump(D, open(os.path.join(HERE, "damage_functions.json"), "w"), indent=1)
print("unit_costs:", len(U) - 1, "items; damage occupancies:", sorted(flood["structure"]))
