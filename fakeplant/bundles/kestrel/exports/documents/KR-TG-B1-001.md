# KRGP train B1: sales gas compressor trip response

## Purpose

Troubleshooting guide for train B1 (compression) at Kestrel Ridge gas plant. Written for the control room and the field operator dispatched to the compressor.

## First minutes

When B1-K-2001 trips, sales gas falls to zero and the inlet separator A1-V-1001 starts to pack. Hold A1-PT-1001 below 27 barg: raise the separator pressure setpoint to 27.5 barg at most and choke back the Pad 12 wells (procedure KR-PR-A1-001). Never let A1-PT-1001 reach the PSV set pressure of 30 barg. While compression is down, the gas the chokes still let through goes to flare through A1-PV-1001 and the flare knockout drum H1-V-8001: read the flared volume from H1-FT-8003 for the flaring report.

## High vibration trip

A trip on B1-VT-2003 above 11.2 mm/s: before restart, check the frame and foundation bolts, crosshead guide temperature and the vibration probe mounting. Two vibration trips in 30 days means a crosshead and bearing clearance check, not another restart. Rod packing set JGK-RP-2 is the usual spare to have on hand.

## High discharge temperature trip

A trip on B1-TT-2002 above 150 degC: the aftercooler B1-E-2010 is air cooled, so check its fan belts and fins for dust before anything else. On hot afternoons the approach rises; clean the fins if B1-TT-2011 runs more than 15 degC above ambient.

## After the restart

Gas returning through the amine contactor D1-V-4001 can foam: watch D1-PDT-4001 and the sweet gas CO2 D1-AT-4002 for two hours (procedure KR-PR-D1-001). The propane compressor E1-K-5004 runs on recycle through a trip; confirm the low temperature separator E1-TT-5003 is back below -15 degC before calling the sales gas on specification at H1-FT-8010.

## Escalation

Call the on-call reliability engineer if the compressor trips again within 24 hours of a restart.
