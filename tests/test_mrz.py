import pytest

from app.mrz import MRZParseError, check_digit, parse_mrz

# ICAO Doc 9303 specimen documents.
TD3_ICAO = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
TD1_ICAO = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]
TD2_ICAO = [
    "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<",
    "D231458907UTO7408122F1204159<<<<<<<6",
]


@pytest.mark.parametrize("data, expected", [("L898902C3", "6"), ("740812", "2"), ("120415", "9"), ("<<<<", "0")])
def test_check_digit(data, expected):
    assert check_digit(data) == expected


def test_parse_td3_passport():
    r = parse_mrz(TD3_ICAO)
    assert r.format == "TD3"
    assert r.valid
    assert (r.document_type, r.issuing_country, r.nationality) == ("P", "UTO", "UTO")
    assert (r.surname, r.given_names) == ("ERIKSSON", "ANNA MARIA")
    assert r.document_number == "L898902C3"
    assert r.date_of_birth == "1974-08-12"
    assert r.expiry_date == "2012-04-15"
    assert r.sex == "F"
    assert r.optional_data == "ZE184226B"


def test_parse_td1_id_card():
    r = parse_mrz(TD1_ICAO)
    assert r.format == "TD1"
    assert r.valid
    assert r.document_number == "D23145890"
    assert (r.surname, r.given_names) == ("ERIKSSON", "ANNA MARIA")
    assert r.date_of_birth == "1974-08-12"


def test_parse_td2():
    r = parse_mrz(TD2_ICAO)
    assert r.format == "TD2"
    assert r.valid
    assert r.document_number == "D23145890"


def test_ocr_confusions_in_numeric_fields_are_corrected():
    # Date of birth 740812 read as 74O8I2, expiry 120415 read as I2O4IS.
    line2 = "L898902C36UTO74O8I22FI2O4IS9ZE184226B<<<<<10"
    r = parse_mrz([TD3_ICAO[0], line2])
    assert r.valid
    assert r.date_of_birth == "1974-08-12"
    assert r.expiry_date == "2012-04-15"


def test_document_number_repaired_using_check_digit():
    # 'L898902C3' with the 0 misread as O.
    line2 = "L8989O2C36UTO7408122F1204159ZE184226B<<<<<10"
    r = parse_mrz([TD3_ICAO[0], line2])
    assert r.document_number == "L898902C3"
    assert r.checks["document_number"]


def test_filler_misread_as_k_in_names():
    line1 = "P<UTOERIKSSON<<ANNA<MARIA<K<<<<<<KKKKKKKKKK"
    r = parse_mrz([line1, TD3_ICAO[1]])
    assert (r.surname, r.given_names) == ("ERIKSSON", "ANNA MARIA")


def test_tampered_mrz_fails_validation():
    line2 = TD3_ICAO[1].replace("7408122", "7508122")  # change birth year, keep old check digit
    r = parse_mrz([TD3_ICAO[0], line2])
    assert not r.valid
    assert not r.checks["date_of_birth"]


def test_short_ocr_line_is_padded():
    r = parse_mrz([TD3_ICAO[0].rstrip("<"), TD3_ICAO[1]])
    assert r.valid
    assert r.given_names == "ANNA MARIA"


def test_wrong_line_count_raises():
    with pytest.raises(MRZParseError):
        parse_mrz(["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"])
