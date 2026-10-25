def apply(city):
    # 1. Mission Creek / 17th & Folsom basin: the lowest open ground
    hot = city.low_points(5)
    p = hot[0]
    box = [[p['x']-250, p['y']-250], [p['x']+250, p['y']-250], [p['x']+250, p['y']+250], [p['x']-250, p['y']+250]]
    # 2. Store and move water: a vault under the low point, a relief tunnel to the bay (east), a pump station
    vault = city.storage(p['x'], p['y'], volume_m3=30000, intake_m3s=4)
    city.tunnel([[p['x'], p['y']], [p['x']+900, p['y']+300], [3500, p['y']+400]], m3s=25)
    city.pump(p['x']+30, p['y'], m3s=3)
    # 3. Slow the runoff upstream: permeable parking lanes + bioswales along the streets that drain into the basin
    for s in city.streets('FOLSOM')[:6]:
        city.bioswale(s['coords'], width_m=2.0)
    city.permeable([[p['x']-600, p['y']+300], [p['x']-300, p['y']+300], [p['x']-300, p['y']+600], [p['x']-600, p['y']+600]])
    # 4. Protect what still floods: dry-floodproof ground-floor businesses, deployable barrier on the bay edge
    city.floodproof(city.buildings_in(box, 'COM'), height_m=0.9)
    city.wall([[3300, 3700], [3500, 3900]], height_m=1.2, kind='deployable')
    # 5. Operations: clear the drains, draw the sewer down, tow the cars, stage pumps
    city.clear_drains(box)
    city.sewer_drawdown(basins=[city.basin_of(p['x'], p['y'])])
    city.tow_cars(box)
    city.deploy_portable_pumps([[q['x'], q['y']] for q in hot[1:4]])
    # 6. Wind: brace big street trees on the windward (SSW) streets, prune the rest
    big = city.trees_in(box, min_dbh_cm=40)
    city.brace(big)
    city.prune(city.trees_in(box)[:200])
    # 7. People: a shelter in a school and an evacuation of the basin (see RESPONSE_API.md)
    school = city.buildings_in(box, 'EDU1')
    if school:
        city.response.shelter(school[0], capacity=300, open_at_s=-6*3600)
    # 8. Your own 3D design for the tunnel portal (no extra cost: it depicts the tunnel item)
    city.log('Mission Creek package: store, convey, protect, operate')
