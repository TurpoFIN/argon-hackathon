import math
import numpy as np

def apply(city):
    # =========================================================================
    # 1. PRE-STORM SEWER & DRAINAGE OPERATIONS (CITYWIDE + HOTSPOT BASINS)
    # =========================================================================
    # Draw down all 24 combined sewer transport/storage boxes to 0% initial fill
    city.sewer_drawdown(
        basins="all",
        frac=0.0,
        label="SFPUC Citywide Combined Sewer Box Drawdown (24 Basins)",
        notes="Pre-drains all 24 sub-sewershed transport/storage boxes from 15% dry-weather fill to 0% before landfall ($40.9k total)."
    )

    # Clear leaf-clogged storm drain inlets across all major flood basins
    hotspot_boxes = [
        ("Mission Creek & 17th/Folsom Basin", [[550, 480], [1300, 480], [1300, 1550], [550, 1550]]),
        ("Division & Lower 7th/8th Basin", [[1250, 1150], [2250, 1150], [2250, 1700], [1250, 1700]]),
        ("Central SoMa & Underpass Basin", [[1450, 1950], [2400, 1950], [2400, 2700], [1450, 2700]]),
        ("Mission Bay & Channel Basin", [[2200, 1300], [3000, 1300], [3000, 2150], [2200, 2150]]),
        ("Folsom & 3rd/4th + Market/5th Sags", [[1380, 2740], [2450, 2740], [2450, 3050], [1380, 3050]]),
        ("NE Embarcadero Waterfront", [[2200, 3900], [2850, 3900], [2850, 4256], [2200, 4256]]),
    ]
    all_clear_inlets = []
    for name, poly in hotspot_boxes:
        all_clear_inlets.extend(city.inlets_in(poly))
    all_clear_inlets = sorted(set(all_clear_inlets))
    city.clear_drains(
        all_clear_inlets,
        label="SFPUC Pre-Storm Catch Basin Leaf Clearing across 6 Flood Basins",
        notes=f"Restores 100% HEC-22 grate capacity across {len(all_clear_inlets)} catch basins in Mission Creek, SoMa, Mission Bay, and Embarcadero."
    )

    # Upgrade inlets in the highest-damage ponding sags where water pools on surface
    sag_inlets = (
        city.inlets_in([[740, 950], [1080, 950], [1080, 1360], [740, 1360]]) +
        city.inlets_in([[1800, 1350], [2200, 1350], [2200, 1600], [1800, 1600]]) +
        city.inlets_in([[2290, 2750], [2420, 2750], [2420, 2890], [2290, 2890]]) +
        city.inlets_in([[1400, 2870], [1540, 2870], [1540, 3030], [1400, 3030]])
    )
    sag_inlets = sorted(set(sag_inlets))
    if sag_inlets:
        city.upgrade_inlets(
            sag_inlets[:100],
            factor=3.0,
            label="High-Capacity Curb-Opening Catch Basin Upgrades at Critical Sags",
            notes=f"Triples inlet interception capacity at {min(100, len(sag_inlets))} catch basins in 17th/Folsom, 7th/Townsend, 3rd/Folsom, and 5th/Market."
        )

    # Add new catch basins at the worst local sags (3rd/Folsom Bldg 8642, 14th/15th & Folsom, 8th/Townsend)
    new_inlet_pts = [
        [2345, 2811], [2355, 2820], [2335, 2805],
        [909, 1110], [896, 1114], [960, 1011], [914, 1304], [970, 1287],
        [1899, 1515], [2105, 1531], [2012, 1431], [1960, 1421],
        [1477, 2951], [2022, 2310], [2430, 1890], [2518, 1840]
    ]
    city.add_inlets(
        new_inlet_pts,
        capacity_m3s=0.08,
        label="16 New High-Capacity Sag Catch Basins at Top Flood Depressions",
        notes="Adds 0.08 m3/s catch basins directly at the 16 deepest street ponding sags across Basins 1, 2, 3, 4, 6, and 21."
    )

    # =========================================================================
    # 2. ENGINEERED GREY & GREEN DRAINAGE INFRASTRUCTURE
    # =========================================================================
    # Permanent Stormwater Pump Stations at the 5 highest-damage basins
    city.pump(
        915, 1180, m3s=2.0,
        label="Mission Creek / 15th & Folsom Permanent Pump Station (2.0 m3/s)",
        notes="Pumps 2.0 m3/s of overland ponding from the 17th & Folsom / Mission Creek bowl ($113.9M baseline damage) directly to the Bay."
    )
    city.pump(
        1960, 1450, m3s=1.5,
        label="Division & 7th/8th Street Permanent Pump Station (1.5 m3/s)",
        notes="Extracts 1.5 m3/s from the Division/Townsend basin ($71.0M baseline damage), protecting buildings 10209, 10210, and 10212."
    )
    city.pump(
        2020, 2335, m3s=1.2,
        label="Central SoMa 5th & Harrison Permanent Pump Station (1.2 m3/s)",
        notes="Removes 1.2 m3/s from the Central SoMa freeway underpass and Perry/Clara/Harrison sag ($34.2M baseline damage)."
    )
    city.pump(
        2450, 1880, m3s=1.0,
        label="Mission Bay / King & 5th Street Permanent Pump Station (1.0 m3/s)",
        notes="Pumps 1.0 m3/s out of the Mission Bay / Channel depression ($34.8M baseline damage), protecting residential towers 2782 and 2783."
    )
    city.pump(
        2345, 2811, m3s=0.6,
        label="3rd & Folsom (Basin 21) Dedicated Pump Station (0.6 m3/s)",
        notes="Drains the closed sag at 2345, 2811 adjacent to Building 8642 ($28.7M #1 single-building loss in baseline)."
    )

    # Underground cistern/storage vault at 17th & Folsom (FASIP complement)
    city.storage(
        909, 1110, volume_m3=1500, intake_m3s=3.0,
        label="17th & Folsom Underground Stormwater Storage Vault (1,500 m3)",
        notes="Captures peak burst runoff at 3.0 m3/s at the centroid of Basin 1 between Buildings 574 and 3769."
    )

    # Portable 12-in diesel trailer pumps (0.368 m3/s * 4 = 1.47 m3/s per point, very cost-effective!)
    portable_pump_locs = [
        [896, 1114], [960, 1011], [914, 1304], [970, 1287], [830, 810], [845, 1239],
        [1899, 1515], [2105, 1531], [2151, 1469], [2012, 1431], [1960, 1421], [1444, 1402],
        [1946, 2334], [1966, 2466], [2314, 2538], [1858, 2530], [1824, 2578],
        [2518, 1840], [2625, 1950], [2441, 1950], [2624, 1485], [2759, 1791],
        [2355, 2820], [1477, 2951], [2496, 4120], [963, 2585]
    ]
    city.deploy_portable_pumps(
        portable_pump_locs, m3s_each=1.47, t_s=-2 * 3600.0,
        label="26 Quad-Bank 12-inch Diesel Trailer Pump Stations (38.2 m3/s Total)",
        notes="Deploys four 12-in centrifugal pumps (1.47 m3/s per site) at 26 critical ponding sags from T-2h through storm end."
    )

    # Bioswales along Folsom St and Harrison St upstream of Mission Creek Basin 1
    city.bioswale(
        [[874, 882], [859, 1062], [845, 1239], [847, 1356]],
        width_m=3.0, depth_m=0.3,
        label="Folsom Street Bioretention Green Infrastructure Corridor (13th to 16th St)",
        notes="480 m x 3.0 m SFPUC bioretention swale (ksat 127 mm/h) intercepting sheet flow along Folsom St into Basin 1."
    )
    city.bioswale(
        [[1056, 1039], [1045, 1183], [1040, 1347]],
        width_m=3.0, depth_m=0.3,
        label="Harrison Street Bioretention Green Infrastructure Corridor (13th to 15th St)",
        notes="310 m x 3.0 m bioretention swale capturing street runoff along Harrison St in the Mission Creek watershed."
    )

    # =========================================================================
    # 3. COASTAL & OVERLAND FLOODWALLS / DEPLOYABLE BARRIERS
    # =========================================================================
    # Low concrete floodwall along NE Embarcadero low apron (Basin 5) where king tide + surge overtops
    city.wall(
        [[2395, 4122], [2502, 4140], [2664, 4129], [2795, 4102]],
        height_m=1.1, kind="floodwall",
        label="Embarcadero North Ferry Plaza Low Concrete Floodwall (1.1 m)",
        notes="Prevents 2.82 m NAVD88 king tide + storm surge from overtopping the low Embarcadero promenade into Basin 5."
    )
    # Channel / 4th St underpass deployable flood barrier where Channel water backs up
    city.wall(
        [[2860, 1985], [2905, 1985]],
        height_m=1.1, kind="deployable",
        label="4th Street / Channel Underpass Deployable AquaFence Barrier (1.1 m)",
        notes="Blocks Mission Creek Channel tidal surge and overland flow from pouring into the 4th St underpass sag."
    )

    # =========================================================================
    # 4. TARGETED BUILDING FLOODPROOFING, SANDBAGGING & CONTENTS PROTECTION
    # =========================================================================
    top_priority_ids = [
        8642, 3769, 574, 10209, 10210, 10212, 583, 2782, 10228, 3770, 10246, 598,
        10948, 9475, 10943, 2783, 8192, 591, 341, 4579, 9476, 10267, 10158, 10058,
        2797, 2754, 10695, 3779, 2972, 3914, 4244, 2764, 2778, 606, 9015, 5966,
        367, 10318, 2824, 588, 10272, 10257, 2809, 4259, 2786, 3939, 10223, 8644,
        2794, 3635, 10074, 3921, 10944, 3036, 4584, 1795, 10289, 2819, 8557, 4575,
        3916, 8650, 353, 2439, 633, 8649, 1453, 4590, 10269, 11042, 3817, 10219,
        9507, 350, 3920, 1187, 9177, 3063, 1176, 6299, 643, 3952, 987, 3930, 4058,
        10236, 10990, 10217, 10928, 10263, 10984, 1456, 4588, 664, 2776, 593, 3118,
        3086, 3066, 3089, 4075, 4085, 4604, 5744, 629, 662, 610, 9184, 9608, 9653,
        9661, 9801, 9837, 10082, 10668, 10709, 10835, 10864, 10920, 11031, 342, 344
    ]

    # Prioritize dry floodproofing by damage/area efficiency, capping footprint at 21,000 m2 (~$21.0M)
    fp_candidates = []
    for idx, bid in enumerate(top_priority_ids):
        if bid not in city._bl:
            continue
        b = city._bl[bid]
        area = max(1.0, float(b.get("area_m2", 100.0)))
        val = float(b.get("value_structure", 0)) + float(b.get("value_contents", 0))
        rank_weight = 5000.0 if idx < 25 else (1000.0 if idx < 60 else 100.0)
        score = rank_weight * (val / area)
        fp_candidates.append((score, bid, area))
    fp_candidates.sort(reverse=True)

    fp_selected = []
    fp_area_sum = 0.0
    for score, bid, area in fp_candidates:
        if fp_area_sum + area <= 21000.0:
            fp_selected.append(bid)
            fp_area_sum += area

    city.floodproof(
        fp_selected,
        height_m=0.9,
        label=f"FEMA P-936 Dry Floodproofing (0.9 m) on {len(fp_selected)} High-Value Buildings",
        notes=f"Seals ground-floor walls, flood shields doorways, and installs backflow valves to 0.9 m on {len(fp_selected)} critical buildings ({fp_area_sum:,.0f} m2)."
    )

    # Elevate contents / de-energize ground floors across low-lying flood-prone buildings (USACE Day curve 0.65)
    all_flood_zone_bldgs = [
        int(b["id"]) for b in city.buildings
        if (float(b.get("value_contents", 0)) > 1000000 and b["ground_z"] < 15.0)
    ]
    city.action(
        name="Citywide Flood Zone Contents Elevation & Ground-Floor De-Energization",
        description="Deploy DPW/DEM advisories and facility crews to elevate ground-floor contents and de-energize basements across flood-prone commercial, industrial, government, and residential buildings (USACE Day-curve 35% reduction).",
        effects={"contents_mult": {"building_ids": all_flood_zone_bldgs, "factor": 0.65}},
        cost_items=[("labour_crew_hr", 400), ("truck_pickup_hr", 120)],
        label="Flood-Zone Contents Elevation & Utility De-Energization Program",
        notes=f"Reduces contents flood damage by 35% (factor 0.65) across {len(all_flood_zone_bldgs)} low-lying buildings via pre-storm elevation."
    )

    # Sandbag giveaway at 14 hotspot hubs (28,000 bags -> protects 1,400 nearest buildings to 0.3 m for $77.8k)
    giveaway_sites = [
        [959, 1010], [896, 1114], [914, 1304], [1899, 1515], [2105, 1531],
        [2012, 1431], [1946, 2334], [1858, 2530], [2518, 1840], [2625, 1950],
        [2355, 2820], [1477, 2951], [2502, 1089], [829, 810]
    ]
    city.sandbag_giveaway(
        giveaway_sites,
        bags=28000,
        label="SF Public Works Pre-Storm Sandbag Giveaway at 14 Hotspot Hubs (28,000 Bags)",
        notes="Distributes 28,000 filled sandbags across 14 flood-basin sites, providing 0.3 m doorway protection to 1,400 nearby buildings."
    )

    # =========================================================================
    # 5. URBAN FORESTRY, VEHICLE TOWING & PRE-POSITIONING OPERATIONS
    # =========================================================================
    # Tow parked cars from all major flood basins before the storm ($652/car vs ~$6,300+ flood/tree loss per car)
    tow_polys = [
        [[600, 500], [1250, 500], [1250, 1500], [600, 1500]],
        [[1650, 2050], [2330, 2050], [2330, 2600], [1650, 2600]],
        [[2200, 1450], [2850, 1450], [2850, 2080], [2200, 2080]],
        [[1750, 1200], [2220, 1200], [2220, 1650], [1750, 1650]],
    ]
    for idx, poly in enumerate(tow_polys):
        city.tow_cars(
            poly,
            label=f"SFMTA Pre-Storm Flood-Zone Tow-Away Operations (Zone {idx+1})",
            notes="Suspends parking and tows vehicles out of deep ponding streets prior to T-6h to prevent vehicle floatation and flood total losses."
        )

    # Prune large street trees to reduce crown drag area by 30% and prevent uprooting/building strikes
    high_risk_trees = []
    for t in city.trees:
        dbh = float(t.get("dbh_cm") or 0)
        h = float(t.get("height_m") or 0)
        if dbh >= 22.0 and h >= 9.0:
            high_risk_trees.append(int(t["id"]))
    city.prune(
        high_risk_trees[:4500],
        label="SF Public Works Pre-Storm Structural Crown Pruning (4,500 Large Street Trees)",
        notes="Reduces crown sail area on 4,500 large-DBH street trees to prevent uprooting, building strikes, and road blockages under 26 m/s gusts."
    )

    # Pre-position vactor trucks and light towers at Mission Creek and SoMa
    city.pre_position(
        "vactor", 950, 1200, n=4, hours=12.0,
        label="SFPUC Vactor 2100 Hydro-Excavation Trucks Staged at Mission Creek",
        notes="Stages 4 vacuum trucks at 17th & Folsom for rapid catch-basin and sewer surcharge relief."
    )
    city.pre_position(
        "light_tower", 1950, 2350, n=6, hours=12.0,
        label="DPW Mobile LED Light Towers Staged in SoMa & Mission Creek",
        notes="Stages 6 Generac 8kW mobile light towers to illuminate night-time barrier, pump, and rescue operations."
    )

    # =========================================================================
    # 6. EMERGENCY RESPONSE PLAN (city.response)
    # =========================================================================
    r = city.response

    # 6.1 Alerts (T-12h early warning and T-4h evacuation alert -> full FD2321 warning lead time >= 2h)
    r.alert(
        "AlertSF/WEA: Extreme El Nino Atmospheric River hitting SF in 12 hours. Flash flooding expected in Mission Creek (17th & Folsom), SoMa, Mission Bay, and Embarcadero. Vulnerable residents evacuate to shelters now; all others move to upper floors.",
        at_s=-12 * 3600,
        zone=None,
        label="Citywide AlertSF & WEA Early Flood Warning (T-12h)",
        notes="Provides 12-hour advance flood warning across all SF neighborhoods, achieving full FD2321 warning score reduction."
    )
    r.alert(
        "AlertSF EMERGENCY: Evacuation Order for ground-floor & vulnerable residents in Mission Creek, SoMa, and Mission Bay. Avoid low streets and underpasses.",
        at_s=-4 * 3600,
        zone=[(500, 450), (3100, 450), (3100, 2900), (500, 2900)],
        label="Targeted Basin Evacuation & Underpass Hazard Alert (T-4h)",
        notes="Directs Mission, SoMa, and Mission Bay residents to congregate shelters and warns drivers off low underpasses before rain intensifies."
    )

    # 6.2 Congregate Shelters on high, dry ground (`np_depth_cm` = 0) with 150 kW Backup Generators
    shelter_specs = [
        (8637, 2500, 15000, 5000, "Moscone Center South Regional Shelter (SoMa High Ground)"),
        (10049, 2500, 15000, 5000, "Civic Center Van Ness Hub Shelter (Western Addition/Civic)"),
        (9304, 1800, 10800, 3600, "Potrero Hill Government Center Shelter (Mission/Potrero)"),
        (8194, 1500, 9000, 3000, "Mission Bay Educational Complex Shelter (Mission Bay)"),
        (7128, 1200, 7200, 2400, "Potrero Hill School Shelter (Southern Hills)"),
        (1175, 2000, 12000, 4000, "Civic/Market Street Government Tower Shelter (Downtown/Tenderloin)"),
    ]
    shelter_ids = []
    for bid, cap, meals, water_gal, sname in shelter_specs:
        sid = r.shelter(
            bid,
            capacity=cap,
            open_at_s=-10 * 3600,
            supplies={"meals": meals, "water_gal": water_gal},
            name=sname,
            label=f"{sname} (Cap: {cap})",
            notes=f"Opens ADA-compliant congregate shelter at T-10h on dry ground with {meals:,} meals and {water_gal:,} gal water pre-stocked."
        )
        shelter_ids.append(sid)
        r.generator(
            bid,
            start_s=-10 * 3600,
            label=f"150 kW Backup Diesel Generator at {sname}",
            notes=f"Ensures uninterrupted emergency power for HVAC, medical equipment, and lighting at {sname}."
        )

    # 6.3 Phased Evacuations (well before T=0 so nobody gets stranded by floodwater > 0.3 m)
    basin1_core = [(640, 600), (1150, 600), (1150, 1420), (640, 1420)]
    r.evacuate(
        basin1_core,
        order_at_s=-6 * 3600,
        to_shelters=shelter_ids,
        priority="all",
        transport="auto",
        label="Mandatory Pre-Landfall Evacuation: Mission Creek / 17th & Folsom Core (T-6h)",
        notes="Evacuates all residents in the deep 17th & Folsom ponding bowl 6 hours before storm start using cars and paratransit buses."
    )

    soma_core = [(1450, 1200), (2360, 1200), (2360, 2620), (1450, 2620)]
    r.evacuate(
        soma_core,
        order_at_s=-5 * 3600,
        to_shelters=shelter_ids,
        priority="all",
        transport="auto",
        label="Mandatory Pre-Landfall Evacuation: Central SoMa & Division/Townsend Basins (T-5h)",
        notes="Evacuates all residents in the Central SoMa and 7th/8th & Townsend flood basins 5 hours before landfall."
    )

    mb_core = [(2220, 1350), (2950, 1350), (2950, 2100), (2220, 2100)]
    r.evacuate(
        mb_core,
        order_at_s=-5 * 3600,
        to_shelters=shelter_ids,
        priority="all",
        transport="auto",
        label="Mandatory Pre-Landfall Evacuation: Mission Bay & Channel Basin (T-5h)",
        notes="Evacuates all residents in the low-lying Mission Bay and Channel corridor 5 hours before landfall."
    )

    broad_zone = [(400, 400), (3300, 400), (3300, 4150), (400, 4150)]
    r.evacuate(
        broad_zone,
        order_at_s=-7 * 3600,
        to_shelters=shelter_ids,
        priority="vulnerable",
        transport="auto",
        label="Early Paratransit & Assisted Evacuation for Vulnerable Populations (T-7h)",
        notes="Evacuates elderly and disabled residents across all low-lying SF neighborhoods at T-7h before any street flooding begins."
    )

    # 6.4 Logistics Depots & Supply Runs
    food_depot = r.depot(
        1350, 2680, kind="food", name="HSA Central Food Logistics Depot (Market/Civic)",
        label="HSA Central Food Distribution Depot (1350, 2680)",
        notes="High-ground food staging depot supplying shelf-stable MREs to all 6 congregate shelters."
    )
    water_depot = r.depot(
        1550, 2680, kind="water", name="SFPUC Emergency Drinking Water Depot (SoMa High Ground)",
        label="SFPUC Bottled Water Distribution Depot (1550, 2680)",
        notes="High-ground water staging depot supplying bottled drinking water to all 6 congregate shelters."
    )
    r.supply_run(
        food_depot, shelter_ids, trucks=6, depart_at_s=-8 * 3600, loop=True,
        label="HSA Shelter Food Resupply Convoy (6 Flatbed Trucks)",
        notes="Continuous looping delivery of meals from the HSA Food Depot to all 6 shelters starting at T-8h."
    )
    r.supply_run(
        water_depot, shelter_ids, trucks=5, depart_at_s=-8 * 3600, loop=True,
        label="SFPUC Shelter Water Resupply Convoy (5 Water Trucks)",
        notes="Continuous looping delivery of drinking water from the SFPUC Water Depot to all 6 shelters starting at T-8h."
    )

    # 6.5 Response Crews (Swiftwater Rescue Boats, Sandbagging, Portable Pumps, Tree Clearance, Medical)
    r.crew(
        "rescue", n=6, stage_at=(1050, 1400), start_s=-1 * 3600,
        label="SFFD Swiftwater Rescue Boat Division Alpha (Mission Creek / 17th & Folsom)",
        notes="6 swiftwater rescue boat teams (24 firefighters) pre-staged at the Mission Creek basin edge."
    )
    r.crew(
        "rescue", n=5, stage_at=(1950, 2250), start_s=-1 * 3600,
        label="SFFD Swiftwater Rescue Boat Division Bravo (Central SoMa & Division)",
        notes="5 swiftwater rescue boat teams pre-staged at 5th & Harrison to cover Central SoMa and Division/Townsend."
    )
    r.crew(
        "rescue", n=5, stage_at=(2500, 1900), start_s=-1 * 3600,
        label="SFFD Swiftwater Rescue Boat Division Charlie (Mission Bay & Channel)",
        notes="5 swiftwater rescue boat teams pre-staged at Mission Bay to cover King St, Berry St, and Channel."
    )
    r.crew(
        "rescue", n=3, stage_at=(2450, 3950), start_s=-1 * 3600,
        label="SFFD Swiftwater Rescue Boat Division Delta (Embarcadero & Downtown)",
        notes="3 swiftwater rescue boat teams pre-staged near Market & Embarcadero for waterfront and downtown rescues."
    )

    sandbag_targets = [3769, 10209] + [
        b for b in top_priority_ids if b not in fp_selected
    ] + r.buildings_in(basin1_core, occupancy_prefix="RES")[:80]
    sandbag_targets = list(dict.fromkeys(sandbag_targets))[:120]
    r.crew(
        "sandbag", n=10, tasks=sandbag_targets, start_s=-12 * 3600,
        label="DPW Sandbagging Strike Teams (10 Crews, 120 Priority Buildings)",
        notes="Places 0.3 m USACE pyramid sandbag barriers across doorways of Building 3769, Building 10209, and 118 basin buildings."
    )

    r.crew(
        "pump", n=6,
        tasks=[(909, 1110), (1960, 1421), (2022, 2310), (2430, 1890), (2345, 2811), (1477, 2951)],
        start_s=-1 * 3600,
        label="SFPUC 6-inch Trash Pump Strike Teams (6 Crews at Hotspot Sags)",
        notes="Operates 6-inch mobile diesel trash pumps (0.095 m3/s each) at the 6 deepest ponding centroids."
    )

    r.crew(
        "tree", n=6, stage_at=(1500, 1900), start_s=-1 * 3600,
        label="DPW Urban Forestry Emergency Road Clearance Teams (6 Crews, Central/South)",
        notes="Clears fallen street trees from evacuation and supply truck routes in Mission, Potrero, and SoMa."
    )
    r.crew(
        "tree", n=4, stage_at=(2200, 2900), start_s=-1 * 3600,
        label="DPW Urban Forestry Emergency Road Clearance Teams (4 Crews, North/East)",
        notes="Clears fallen street trees from Embarcadero, Market St, and Downtown supply corridors."
    )

    r.medical_post(
        1150, 1500, start_s=-2 * 3600, name="Mission Creek Field Medical Post (11th & Folsom)",
        label="DPH Field Medical Triage Post & Ambulance (Mission Creek)",
        notes="Treats and stabilizes evacuees and swiftwater rescue survivors from the Mission Creek and Division basins."
    )
    r.medical_post(
        2150, 2400, start_s=-2 * 3600, name="SoMa / Mission Bay Field Medical Post (4th & Harrison)",
        label="DPH Field Medical Triage Post & Ambulance (SoMa / Mission Bay)",
        notes="Treats and stabilizes survivors from Central SoMa, Mission Bay, and Downtown."
    )
    r.crew(
        "medical", n=4, stage_at=(1150, 1500), start_s=-2 * 3600,
        label="DPH Emergency Medical Strike Team (4 Crews)",
        notes="Provides field triage, hypothermia treatment, and shelter medical support."
    )

    # 6.6 Road Closures at Deep Underpasses & Flood Sags (prevents trapped drivers)
    road_closures = [
        ([(750, 1239), (1050, 1239)], "14th Street Flood Sag Closure (Shotwell to Harrison)"),
        ([(750, 1062), (1060, 1062)], "15th Street Flood Sag Closure (Shotwell to Harrison)"),
        ([(840, 1050), (855, 1360)], "Folsom Street Basin 1 Closure (13th to 15th St)"),
        ([(1035, 1050), (1050, 1360)], "Harrison Street Basin 1 Closure (13th to 15th St)"),
        ([(1880, 2260), (2060, 2420)], "Harrison Street & Merlin/Oak Grove Underpass Closure (5th to 6th St)"),
        ([(1920, 2430), (2020, 2510)], "5th Street & Clara/Shipley Underpass Closure"),
        ([(2280, 2500), (2390, 2580)], "4th Street & Stillman I-80 Underpass Closure"),
        ([(1850, 1380), (2150, 1540)], "Division & King/Townsend Underpass Closure (7th to 8th St)"),
        ([(2400, 1580), (2550, 1670)], "6th Street / Channel Underpass Closure"),
        ([(2820, 1930), (2930, 2030)], "4th Street / Channel Bridge Approach Closure"),
        ([(1450, 2940), (1540, 3030)], "5th & Market / Eddy Depression Closure"),
    ]
    for pl, cname in road_closures:
        r.close_road(
            pl, at_s=-1 * 3600,
            label=f"SFPD/SFMTA Barricade Closure: {cname}",
            notes=f"Closes {cname} at T-1h before flooding exceeds 0.3 m so drivers reroute safely and do not become trapped."
        )

    city.log("Complete SF El Nino AR 2026 Resilience & Emergency Operations Plan applied.")
