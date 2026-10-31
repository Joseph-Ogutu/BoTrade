import pytest

from derivbot.gate1.numerics import chi2_sf, norm_sf

# Reference values computed with scipy.stats in a separate environment (scipy is not a dependency of this project).
CHI2 = [
    (0.0, 9, 1.0),
    (3.0, 9, 0.9642949726850891),
    (16.919, 9, 0.049999640848349826),
    (30.0, 9, 0.00043872177097947936),
    (80.0, 9, 1.61613141565346e-13),
    (5.0, 10, 0.8911780189141513),
    (18.307, 10, 0.05000058909139812),
    (40.0, 10, 1.694474393006737e-05),
    (150.0, 10, 3.727485055062505e-27),
    (60.0, 81, 0.9612416464552193),
    (103.01, 81, 0.04999660551315287),
    (120.0, 81, 0.003195142688024627),
    (200.0, 81, 4.732682589050208e-12),
    (300.0, 81, 6.816066593642319e-27),
]
NORM = [
    (0.0, 0.5),
    (0.5, 0.3085375387259869),
    (1.96, 0.024997895148220435),
    (3.0, 0.0013498980316300933),
    (5.0, 2.866515718791933e-07),
    (8.0, 6.22096057427174e-16),
]


@pytest.mark.parametrize("x,df,expected", CHI2)
def test_chi2_sf_matches_scipy(x, df, expected):
    assert chi2_sf(x, df) == pytest.approx(expected, rel=1e-9, abs=0)


@pytest.mark.parametrize("z,expected", NORM)
def test_norm_sf_matches_scipy(z, expected):
    assert norm_sf(z) == pytest.approx(expected, rel=1e-9)


def test_chi2_sf_is_monotone_and_bounded():
    prev = 1.0
    for x in [0, 1, 5, 20, 60, 100, 200]:
        v = chi2_sf(float(x), 81)
        assert 0.0 <= v <= prev
        prev = v


def test_chi2_sf_rejects_bad_arguments():
    with pytest.raises(ValueError):
        chi2_sf(1.0, 0)
    with pytest.raises(ValueError):
        chi2_sf(-1.0, 3)
