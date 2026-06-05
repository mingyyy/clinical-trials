"""
Five test patient profiles for the clinical trial matching comparison.
Each profile tests a different failure mode.

Usage:
    from pipeline.test_profiles import TEST_PROFILES
    for patient in TEST_PROFILES:
        ...
"""

from pipeline.patient_schema import PatientProfile

TEST_PROFILES: list[PatientProfile] = [
    PatientProfile(
        patient_id="P001",
        age=52,
        sex="FEMALE",
        diagnosis="HER2-positive breast cancer, stage II",
        biomarkers=["HER2+", "ER+", "PR-"],
        prior_treatments=["surgery", "chemotherapy"],
        ecog_ps=0,
        location="New York, NY",
        lat=40.7128,
        lon=-74.0060,
        search_condition="HER2-positive breast cancer",
        notes="Baseline case. Should match several HER2+ trials in NYC area.",
    ),
    PatientProfile(
        patient_id="P002",
        age=34,
        sex="FEMALE",
        diagnosis="Triple-negative breast cancer (TNBC), stage III",
        biomarkers=["ER-", "PR-", "HER2-", "BRCA1 mutant"],
        prior_treatments=["neoadjuvant chemotherapy"],
        ecog_ps=1,
        location="Los Angeles, CA",
        lat=34.0522,
        lon=-118.2437,
        search_condition="triple negative breast cancer",
        notes="Biomarker eligibility test. BRCA1 opens some trials, TNBC excludes HER2+ trials.",
    ),
    PatientProfile(
        patient_id="P003",
        age=61,
        sex="FEMALE",
        diagnosis="HR-positive, HER2-negative breast cancer, post-mastectomy",
        biomarkers=["ER+", "PR+", "HER2-"],
        prior_treatments=["mastectomy", "radiation", "tamoxifen (5yr)"],
        ecog_ps=1,
        location="Chicago, IL",
        lat=41.8781,
        lon=-87.6298,
        search_condition="hormone receptor positive breast cancer",
        notes="Preference reasoning test. Patient prefers oral agents, avoids infusion. "
              "Framework should surface this if it can reason about patient preferences.",
    ),
    PatientProfile(
        patient_id="P004",
        age=55,
        sex="MALE",
        diagnosis="Metastatic melanoma with brain metastases",
        biomarkers=["BRAF V600E mutant"],
        prior_treatments=["ipilimumab", "nivolumab"],
        ecog_ps=2,
        location="Seattle, WA",
        lat=47.6062,
        lon=-122.3321,
        search_condition="metastatic melanoma",
        notes="Hard exclusion test. Brain mets is a common exclusion criterion. "
              "High ECOG may also disqualify. Most trials should be excluded.",
    ),
    PatientProfile(
        patient_id="P005",
        age=58,
        sex="FEMALE",
        diagnosis="HER2-positive metastatic breast cancer",
        biomarkers=["HER2+", "ER-"],
        prior_treatments=["trastuzumab", "pertuzumab", "T-DM1"],
        ecog_ps=1,
        location="Boston, MA",
        lat=42.3601,
        lon=-71.0589,
        notes="Multi-line history test. Two prior HER2-targeted lines completed. "
              "Some trials require exactly 1-2 prior lines; framework must count correctly.",
    ),
]
