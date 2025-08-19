import json
from datetime import datetime
from types import SimpleNamespace
from typing import Literal

import boto3
import pytest
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import BatchStatus, BatchType, EmbeddingStatus
from receipt_dynamo.entities import (
    BatchSummary,
    Receipt,
    ReceiptMetadata,
    ReceiptWord,
)

from receipt_label.embedding.word import poll as poll_batch
from receipt_label.embedding.word import submit as submit_batch


@pytest.fixture
def receipt_words():
    """
    Create sample receipt words.

    This was done by listing the first 5 words from 5 r
    """
    return [
        ReceiptWord(
            receipt_id=1,
            image_id="2c9b770c-9407-4cdc-b0eb-3a5b27f0af15",
            line_id=1,
            word_id=1,
            text="REG#12",
            bounding_box={
                "x": -7.86290298002e-09,
                "width": 0.7258064516129032,
                "y": 0.9825581396655705,
                "height": 0.014542108739617476,
            },
            top_right={"x": 0.7258064437500003, "y": 0.997100248405188},
            top_left={"x": -7.86290298002e-09, "y": 0.997100248405188},
            bottom_right={"x": 0.7258064437500003, "y": 0.9825581396655705},
            bottom_left={"x": -7.86290298002e-09, "y": 0.9825581396655705},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="2c9b770c-9407-4cdc-b0eb-3a5b27f0af15",
            line_id=1,
            word_id=2,
            text="TRN#5155",
            bounding_box={
                "x": -7.86290298002e-09,
                "width": 0.7258064516129032,
                "y": 0.9825581396655705,
                "height": 0.014542108739617476,
            },
            top_right={"x": 0.7258064437500003, "y": 0.997100248405188},
            top_left={"x": -7.86290298002e-09, "y": 0.997100248405188},
            bottom_right={"x": 0.7258064437500003, "y": 0.9825581396655705},
            bottom_left={"x": -7.86290298002e-09, "y": 0.9825581396655705},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="2c9b770c-9407-4cdc-b0eb-3a5b27f0af15",
            line_id=1,
            word_id=3,
            text="CSHR#2258458",
            bounding_box={
                "x": -7.86290298002e-09,
                "width": 0.7258064516129032,
                "y": 0.9825581396655705,
                "height": 0.014542108739617476,
            },
            top_right={"x": 0.7258064437500003, "y": 0.997100248405188},
            top_left={"x": -7.86290298002e-09, "y": 0.997100248405188},
            bottom_right={"x": 0.7258064437500003, "y": 0.9825581396655705},
            bottom_left={"x": -7.86290298002e-09, "y": 0.9825581396655705},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="2c9b770c-9407-4cdc-b0eb-3a5b27f0af15",
            line_id=2,
            word_id=1,
            text="STR#9715",
            bounding_box={
                "x": 0.7256191066739365,
                "width": 0.23021339381345407,
                "y": 0.9836203751498578,
                "height": 0.015317389070246934,
            },
            top_right={"x": 0.9554494310490714, "y": 0.9989377642201047},
            top_left={"x": 0.7256191066739365, "y": 0.9981546024468023},
            bottom_right={"x": 0.9558325004873905, "y": 0.9844035369231602},
            bottom_left={"x": 0.7260021761122555, "y": 0.9836203751498578},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="2c9b770c-9407-4cdc-b0eb-3a5b27f0af15",
            line_id=3,
            word_id=1,
            text="Helped",
            bounding_box={
                "x": 2.88978490292e-09,
                "width": 0.5201612903225807,
                "y": 0.9548467274041638,
                "height": 0.016155758077879057,
            },
            top_right={"x": 0.5201612932123656, "y": 0.9710024854820428},
            top_left={"x": 2.88978490292e-09, "y": 0.9710024854820428},
            bottom_right={"x": 0.5201612932123656, "y": 0.9548467274041638},
            bottom_left={"x": 2.88978490292e-09, "y": 0.9548467274041638},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="ae0d9a91-ee91-4b88-aa68-881799eb9ab2",
            line_id=1,
            word_id=1,
            text="05/17/2024",
            bounding_box={
                "x": 0.012499996875000096,
                "width": 0.25,
                "y": 0.9822404371843705,
                "height": 0.013661201997379302,
            },
            top_right={"x": 0.2624999968750001, "y": 0.9959016391817498},
            top_left={"x": 0.012499996875000096, "y": 0.9959016391817498},
            bottom_right={"x": 0.2624999968750001, "y": 0.9822404371843705},
            bottom_left={"x": 0.012499996875000096, "y": 0.9822404371843705},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="ae0d9a91-ee91-4b88-aa68-881799eb9ab2",
            line_id=2,
            word_id=1,
            text="MASTERCARD",
            bounding_box={
                "x": 0.012500000096652789,
                "width": 0.25416666284240874,
                "y": 0.9658469942573894,
                "height": 0.015027322824018974,
            },
            top_right={"x": 0.26666666293906155, "y": 0.9808743170814084},
            top_left={"x": 0.012500000096652789, "y": 0.9808743170814084},
            bottom_right={"x": 0.26666666293906155, "y": 0.9658469942573894},
            bottom_left={"x": 0.012500000096652789, "y": 0.9658469942573894},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="ae0d9a91-ee91-4b88-aa68-881799eb9ab2",
            line_id=3,
            word_id=1,
            text="CARD",
            bounding_box={
                "x": 0.012411470067045466,
                "width": 0.17517705549273574,
                "y": 0.950696735933405,
                "height": 0.013907074438516687,
            },
            top_right={"x": 0.18740987535149684, "y": 0.9646038103719217},
            top_left={"x": 0.012411470067045466, "y": 0.9643578125504304},
            bottom_right={"x": 0.1875885255597812, "y": 0.9509427337548964},
            bottom_left={"x": 0.012590120275329849, "y": 0.950696735933405},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="ae0d9a91-ee91-4b88-aa68-881799eb9ab2",
            line_id=3,
            word_id=2,
            text="#:",
            bounding_box={
                "x": 0.012411470067045466,
                "width": 0.17517705549273574,
                "y": 0.950696735933405,
                "height": 0.013907074438516687,
            },
            top_right={"x": 0.18740987535149684, "y": 0.9646038103719217},
            top_left={"x": 0.012411470067045466, "y": 0.9643578125504304},
            bottom_right={"x": 0.1875885255597812, "y": 0.9509427337548964},
            bottom_left={"x": 0.012590120275329849, "y": 0.950696735933405},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="ae0d9a91-ee91-4b88-aa68-881799eb9ab2",
            line_id=4,
            word_id=1,
            text="PURCHASE",
            bounding_box={
                "x": 0.016470286989967303,
                "width": 0.20039275893591402,
                "y": 0.9341401434746789,
                "height": 0.015599494501519762,
            },
            top_right={"x": 0.21646268436524144, "y": 0.9497396379761986},
            top_left={"x": 0.016470286989967303, "y": 0.9491668934672062},
            bottom_right={"x": 0.21686304592588132, "y": 0.9347128879836714},
            bottom_left={"x": 0.016870648550607183, "y": 0.9341401434746789},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=2,
            image_id="909a2b84-0b9d-496a-adf9-16588602a055",
            line_id=1,
            word_id=1,
            text="COSTCO",
            bounding_box={
                "x": 0.10369903334179827,
                "width": 0.8342686059339991,
                "y": 0.9440428256411028,
                "height": 0.0567419350147248,
            },
            top_right={"x": 0.9370200828701002, "y": 1.0007847606558276},
            top_left={"x": 0.10369903334179827, "y": 0.9992143451590013},
            bottom_right={"x": 0.9379676392757974, "y": 0.9456132411379289},
            bottom_left={"x": 0.1046465897474955, "y": 0.9440428256411028},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=2,
            image_id="909a2b84-0b9d-496a-adf9-16588602a055",
            line_id=2,
            word_id=1,
            text="WHOLESALE",
            bounding_box={
                "x": 0.3666666735392471,
                "width": 0.549999992802458,
                "y": 0.9241379312332249,
                "height": 0.02206896543502812,
            },
            top_right={"x": 0.9166666663417051, "y": 0.946206896668253},
            top_left={"x": 0.3666666735392471, "y": 0.946206896668253},
            bottom_right={"x": 0.9166666663417051, "y": 0.9241379312332249},
            bottom_left={"x": 0.3666666735392471, "y": 0.9241379312332249},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=2,
            image_id="909a2b84-0b9d-496a-adf9-16588602a055",
            line_id=3,
            word_id=1,
            text="Westlake",
            bounding_box={
                "x": 0.23749999121354345,
                "width": 0.5583333429300559,
                "y": 0.8924137934352726,
                "height": 0.01517241299152372,
            },
            top_right={"x": 0.7958333341435994, "y": 0.9075862064267963},
            top_left={"x": 0.23749999121354345, "y": 0.9075862064267963},
            bottom_right={"x": 0.7958333341435994, "y": 0.8924137934352726},
            bottom_left={"x": 0.23749999121354345, "y": 0.8924137934352726},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=2,
            image_id="909a2b84-0b9d-496a-adf9-16588602a055",
            line_id=3,
            word_id=2,
            text="Village",
            bounding_box={
                "x": 0.23749999121354345,
                "width": 0.5583333429300559,
                "y": 0.8924137934352726,
                "height": 0.01517241299152372,
            },
            top_right={"x": 0.7958333341435994, "y": 0.9075862064267963},
            top_left={"x": 0.23749999121354345, "y": 0.9075862064267963},
            bottom_right={"x": 0.7958333341435994, "y": 0.8924137934352726},
            bottom_left={"x": 0.23749999121354345, "y": 0.8924137934352726},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=2,
            image_id="909a2b84-0b9d-496a-adf9-16588602a055",
            line_id=3,
            word_id=3,
            text="#117",
            bounding_box={
                "x": 0.23749999121354345,
                "width": 0.5583333429300559,
                "y": 0.8924137934352726,
                "height": 0.01517241299152372,
            },
            top_right={"x": 0.7958333341435994, "y": 0.9075862064267963},
            top_left={"x": 0.23749999121354345, "y": 0.9075862064267963},
            bottom_right={"x": 0.7958333341435994, "y": 0.8924137934352726},
            bottom_left={"x": 0.23749999121354345, "y": 0.8924137934352726},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            line_id=1,
            word_id=1,
            text="HARBOR",
            bounding_box={
                "x": 0.0865440008290395,
                "width": 0.8398053151256635,
                "y": 0.968917495735272,
                "height": 0.028380920046629732,
            },
            top_right={"x": 0.924668905821485, "y": 0.9972984157819017},
            top_left={"x": 0.0865440008290395, "y": 0.9934465176444696},
            bottom_right={"x": 0.9263493159547029, "y": 0.9727693938727041},
            bottom_left={"x": 0.08822441096225736, "y": 0.968917495735272},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            line_id=1,
            word_id=2,
            text="FREIGHT",
            bounding_box={
                "x": 0.0865440008290395,
                "width": 0.8398053151256635,
                "y": 0.968917495735272,
                "height": 0.028380920046629732,
            },
            top_right={"x": 0.924668905821485, "y": 0.9972984157819017},
            top_left={"x": 0.0865440008290395, "y": 0.9934465176444696},
            bottom_right={"x": 0.9263493159547029, "y": 0.9727693938727041},
            bottom_left={"x": 0.08822441096225736, "y": 0.968917495735272},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            line_id=1,
            word_id=3,
            text="QUALITY",
            bounding_box={
                "x": 0.09513074193233559,
                "width": 0.8056295142983491,
                "y": 0.9538418352642335,
                "height": 0.017031292108478646,
            },
            top_right={"x": 0.8998545895194892, "y": 0.9708731273727121},
            top_left={"x": 0.09513074193233559, "y": 0.9672179914134528},
            bottom_right={"x": 0.9007602562306847, "y": 0.9574969712234929},
            bottom_left={"x": 0.09603640864353105, "y": 0.9538418352642335},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            line_id=2,
            word_id=2,
            text="TOOLS",
            bounding_box={
                "x": 0.09513074193233559,
                "width": 0.8056295142983491,
                "y": 0.9538418352642335,
                "height": 0.017031292108478646,
            },
            top_right={"x": 0.8998545895194892, "y": 0.9708731273727121},
            top_left={"x": 0.09513074193233559, "y": 0.9672179914134528},
            bottom_right={"x": 0.9007602562306847, "y": 0.9574969712234929},
            bottom_left={"x": 0.09603640864353105, "y": 0.9538418352642335},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            line_id=2,
            word_id=3,
            text="LOWESTIPRICES",
            bounding_box={
                "x": 0.09513074193233559,
                "width": 0.8056295142983491,
                "y": 0.9538418352642335,
                "height": 0.017031292108478646,
            },
            top_right={"x": 0.8998545895194892, "y": 0.9708731273727121},
            top_left={"x": 0.09513074193233559, "y": 0.9672179914134528},
            bottom_right={"x": 0.9007602562306847, "y": 0.9574969712234929},
            bottom_left={"x": 0.09603640864353105, "y": 0.9538418352642335},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="41885f70-0a10-4897-8840-8f7373d150cd",
            line_id=1,
            word_id=1,
            text="OSTCO",
            bounding_box={
                "x": 0.30416666877604176,
                "width": 0.675,
                "y": 0.9491315132267368,
                "height": 0.04590570815415662,
            },
            top_right={"x": 0.9791666687760417, "y": 0.9950372213808935},
            top_left={"x": 0.30416666877604176, "y": 0.9950372213808935},
            bottom_right={"x": 0.9791666687760417, "y": 0.9491315132267368},
            bottom_left={"x": 0.30416666877604176, "y": 0.9491315132267368},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="41885f70-0a10-4897-8840-8f7373d150cd",
            line_id=2,
            word_id=1,
            text="WHOLESALE",
            bounding_box={
                "x": 0.3688787761649323,
                "width": 0.5876231365893261,
                "y": 0.9287012152188561,
                "height": 0.027622075022678882,
            },
            top_right={"x": 0.9565019127542583, "y": 0.9494934835049976},
            top_left={"x": 0.37161432774625763, "y": 0.956323290241535},
            bottom_right={"x": 0.9537663611729331, "y": 0.9287012152188561},
            bottom_left={"x": 0.3688787761649323, "y": 0.9355310219553934},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="41885f70-0a10-4897-8840-8f7373d150cd",
            line_id=3,
            word_id=1,
            text="westlake",
            bounding_box={
                "x": 0.262500011836361,
                "width": 0.5708333210772778,
                "y": 0.8969849243278857,
                "height": 0.017407135234320692,
            },
            top_right={"x": 0.8333333329136389, "y": 0.9143920595622064},
            top_left={"x": 0.262500011836361, "y": 0.9143920595622064},
            bottom_right={"x": 0.8333333329136389, "y": 0.8969849243278857},
            bottom_left={"x": 0.262500011836361, "y": 0.8969849243278857},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="41885f70-0a10-4897-8840-8f7373d150cd",
            line_id=3,
            word_id=2,
            text="Village",
            bounding_box={
                "x": 0.262500011836361,
                "width": 0.5708333210772778,
                "y": 0.8969849243278857,
                "height": 0.017407135234320692,
            },
            top_right={"x": 0.8333333329136389, "y": 0.9143920595622064},
            top_left={"x": 0.262500011836361, "y": 0.9143920595622064},
            bottom_right={"x": 0.8333333329136389, "y": 0.8969849243278857},
            bottom_left={"x": 0.262500011836361, "y": 0.8969849243278857},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
        ReceiptWord(
            receipt_id=1,
            image_id="41885f70-0a10-4897-8840-8f7373d150cd",
            line_id=3,
            word_id=3,
            text="#117",
            bounding_box={
                "x": 0.262500011836361,
                "width": 0.5708333210772778,
                "y": 0.8969849243278857,
                "height": 0.017407135234320692,
            },
            top_right={"x": 0.8333333329136389, "y": 0.9143920595622064},
            top_left={"x": 0.262500011836361, "y": 0.9143920595622064},
            bottom_right={"x": 0.8333333329136389, "y": 0.8969849243278857},
            bottom_left={"x": 0.262500011836361, "y": 0.8969849243278857},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=1.0,
            embedding_status="NONE",
        ),
    ]


@pytest.fixture
def receipt_word_labels():
    return [
        ReceiptWordLabel(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="MERCHANT_NAME",
            validation_status=ValidationStatus.VALID,
            timestamp_added=datetime.now(),
        ),
        ReceiptWordLabel(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="ADDRESS",
            validation_status=ValidationStatus.PENDING,
            timestamp_added=datetime.now(),
        ),
        ReceiptWordLabel(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=1,
            line_id=1,
            word_id=1,
            label="ADDRESS_LINE",
            validation_status=ValidationStatus.PENDING,
            timestamp_added=datetime.now(),
        ),
    ]


@pytest.fixture
def receipt_and_metadata():
    receipts = [
        Receipt(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=1,
            width=100,
            height=100,
            timestamp_added=datetime.now(),
            raw_s3_bucket="test_bucket",
            raw_s3_key="test_key",
            top_left={"x": 0, "y": 0},
            top_right={"x": 100, "y": 0},
            bottom_left={"x": 0, "y": 100},
            bottom_right={"x": 100, "y": 100},
            sha256="test_sha256",
            cdn_s3_bucket="test_cdn_bucket",
            cdn_s3_key="test_cdn_key",
        ),
        Receipt(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=2,
            width=100,
            height=100,
            timestamp_added=datetime.now(),
            raw_s3_bucket="test_bucket",
            raw_s3_key="test_key",
            top_left={"x": 0, "y": 0},
            top_right={"x": 100, "y": 0},
            bottom_left={"x": 0, "y": 100},
            bottom_right={"x": 100, "y": 100},
            sha256="test_sha256",
            cdn_s3_bucket="test_cdn_bucket",
            cdn_s3_key="test_cdn_key",
        ),
        Receipt(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=3,
            width=100,
            height=100,
            timestamp_added=datetime.now(),
            raw_s3_bucket="test_bucket",
            raw_s3_key="test_key",
            top_left={"x": 0, "y": 0},
            top_right={"x": 100, "y": 0},
            bottom_left={"x": 0, "y": 100},
            bottom_right={"x": 100, "y": 100},
            sha256="test_sha256",
            cdn_s3_bucket="test_cdn_bucket",
            cdn_s3_key="test_cdn_key",
        ),
    ]
    metadatas = [
        ReceiptMetadata(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=1,
            place_id="test_place_id",
            merchant_name="test_merchant_name",
            matched_fields=["test_field1", "test_field2"],
            timestamp=datetime.now(),
            merchant_category="test_merchant_category",
            address="test_address",
            phone_number="test_phone_number",
            validated_by="NEARBY_LOOKUP",
            reasoning="test_reasoning",
        ),
        ReceiptMetadata(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=2,
            place_id="test_place_id",
            merchant_name="test_merchant_name",
            matched_fields=["test_field1", "test_field2"],
            timestamp=datetime.now(),
            merchant_category="test_merchant_category",
            address="test_address",
            phone_number="test_phone_number",
            validated_by="NEARBY_LOOKUP",
            reasoning="test_reasoning",
        ),
        ReceiptMetadata(
            image_id="29c1d8af-035c-431f-9d80-e4053cf28a00",
            receipt_id=3,
            place_id="test_place_id",
            merchant_name="test_merchant_name",
            matched_fields=["test_field1", "test_field2"],
            timestamp=datetime.now(),
            merchant_category="test_merchant_category",
            address="test_address",
            phone_number="test_phone_number",
            validated_by="NEARBY_LOOKUP",
            reasoning="test_reasoning",
        ),
    ]
    return receipts, metadatas


@pytest.mark.integration
def test_embedding_batch_poll(
    dynamodb_table_and_s3_bucket: tuple[str, str],
    receipt_words: list[ReceiptWord],
    receipt_and_metadata: tuple[Receipt, ReceiptMetadata],
    mocker,
    patch_clients,
):
    # Arrange: point the handler at your Moto table
    # Get our mocked clients
    fake_openai, fake_index = patch_clients
    dynamo_table, s3_bucket = dynamodb_table_and_s3_bucket
    moto_client = DynamoClient(dynamo_table)

    # Create a custom mock client manager for this test
    mock_client_manager = mocker.Mock()
    mock_client_manager.dynamo = moto_client
    mock_client_manager.openai = fake_openai
    mock_client_manager.pinecone = fake_index

    # Patch get_client_manager specifically for the embedding module
    mocker.patch(
        "receipt_label.embedding.word.poll.get_client_manager",
        return_value=mock_client_manager,
    )

    for word in receipt_words:
        word.embedding_status = EmbeddingStatus.PENDING
    moto_client.add_receipt_words(receipt_words)
    batch_id = submit_batch.generate_batch_id()
    moto_client.add_batch_summary(
        BatchSummary(
            batch_id=batch_id,
            batch_type=BatchType.EMBEDDING,
            result_file_id="fake-result-file-id",
            openai_batch_id="fake-batch-id",
            submitted_at=datetime.now(),
            status=BatchStatus.PENDING,
            receipt_refs=[
                ("29c1d8af-035c-431f-9d80-e4053cf28a00", 1),
                ("29c1d8af-035c-431f-9d80-e4053cf28a00", 2),
                ("29c1d8af-035c-431f-9d80-e4053cf28a00", 3),
            ],
        )
    )
    moto_client.add_receipts(receipt_and_metadata[0])
    moto_client.add_receipt_metadatas(receipt_and_metadata[1])
    test_metadata = moto_client.get_receipt_metadata(
        "29c1d8af-035c-431f-9d80-e4053cf28a00", 1
    )

    # Act
    pending_batches = poll_batch.list_pending_embedding_batches()
    batch_status = poll_batch.get_openai_batch_status(
        pending_batches[0].openai_batch_id
    )
    downloaded_results = poll_batch.download_openai_batch_result(
        pending_batches[0].openai_batch_id
    )
    receipt_descriptions = poll_batch.get_receipt_descriptions(
        downloaded_results
    )
    upserted_vectors_count = poll_batch.upsert_embeddings_to_pinecone(
        downloaded_results, receipt_descriptions
    )
    embedding_results_count = poll_batch.write_embedding_results_to_dynamo(
        downloaded_results, receipt_descriptions, pending_batches[0].batch_id
    )
    poll_batch.mark_batch_complete(pending_batches[0].batch_id)

    # Verify the pending batch is pulled from DynamoDB
    assert len(pending_batches) == 1

    # Verify Pinecone index is called twice. Once to get teh completion status and then again to get the file ID
    fake_openai.batches.retrieve.assert_has_calls(
        [
            mocker.call("fake-batch-id"),
            mocker.call("fake-batch-id"),
        ]
    )

    # Verify the returned upserted_vectors_count matches the number of vectors
    assert upserted_vectors_count == len(downloaded_results)
    assert (
        downloaded_results[0]["custom_id"]
        == "IMAGE#29c1d8af-035c-431f-9d80-e4053cf28a00#RECEIPT#00001#LINE#00001#WORD#00001"
    )

    # Verify upsert was called with vectors matching downloaded_results and correct metadata
    called_args, called_kwargs = fake_index.upsert.call_args
    # Extract the vectors argument safely
    if "vectors" in called_kwargs:
        vectors_arg = called_kwargs["vectors"]
    else:
        vectors_arg = called_args[0]
    assert isinstance(vectors_arg, list)
    assert len(vectors_arg) == len(downloaded_results)
    # Unpack the receipt_and_metadata fixture
    receipts, metadatas = receipt_and_metadata
    for vec, result in zip(vectors_arg, downloaded_results):
        # ID and values check
        assert vec["id"] == result["custom_id"]
        assert vec["values"] == result["embedding"]
        # Metadata structure
        meta = vec["metadata"]
        parts = result["custom_id"].split("#")
        assert meta["image_id"] == parts[1]
        assert meta["receipt_id"] == int(parts[3])
        assert meta["line_id"] == int(parts[5])
        assert meta["word_id"] == int(parts[7])
        assert meta["source"] == "openai_embedding_batch"
        # Merchant name from ReceiptMetadata fixture
        matching_meta = next(
            (m for m in metadatas if m.receipt_id == meta["receipt_id"]), None
        )
        assert matching_meta is not None

    # Verify the batch summary is updated to completed
    assert (
        moto_client.get_batch_summary(batch_id).status
        == BatchStatus.COMPLETED.value
    )

    # Verify the EmbeddingBatchResult objects are created and added to DynamoDB
    assert embedding_results_count == len(downloaded_results)
    for result in downloaded_results:
        parts = result["custom_id"].split("#")
        image_id = parts[1]
        receipt_id = int(parts[3])
        line_id = int(parts[5])
        word_id = int(parts[7])
        embedding_result = moto_client.get_embedding_batch_result(
            batch_id=batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
        )
        assert embedding_result is not None
