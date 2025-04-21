import json
from typing import Literal
import pytest
import boto3
from types import SimpleNamespace
from receipt_label.submit_embedding_batch import submit_batch
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptWord


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
            line_id=2,
            word_id=1,
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


@pytest.mark.integration
def test_embedding_batch(
    dynamodb_table_and_s3_bucket: tuple[str, str],
    receipt_words: list[ReceiptWord],
    mocker,
):
    # Arrange: point the handler at your Moto table
    dynamo_table, s3_bucket = dynamodb_table_and_s3_bucket
    moto_client = DynamoClient(dynamo_table)
    # Monkey‑patch the global in the submit_batch module
    mocker.patch.object(submit_batch, "dynamo_client", moto_client)

    # Populate the table
    moto_client.addWords(receipt_words)

    # Act
    words_without_embeddings = (
        submit_batch.list_receipt_words_with_no_embeddings()
    )
    batches = submit_batch.chunk_into_embedding_batches(
        words_without_embeddings
    )
    serialized_words = submit_batch.upload_serialized_words(
        submit_batch.serialize_receipt_words(batches), s3_bucket
    )
    # Treat the following as batch 1
    event = serialized_words[0]
    all_word_in_receipt = submit_batch.query_receipt_words(
        event["image_id"], event["receipt_id"]
    )
    serialized_word_file = submit_batch.download_serialized_words(event)
    deserialized_words = submit_batch.deserialize_receipt_words(
        serialized_word_file
    )
    batch_id = submit_batch.generate_batch_id()
    formatted_words = submit_batch.format_word_context_embedding(
        deserialized_words, all_word_in_receipt
    )
    input_file = submit_batch.write_ndjson(batch_id, formatted_words)
    openai_file = submit_batch.upload_to_openai(input_file)
    openai_batch = submit_batch.submit_openai_batch(openai_file.id)
    batch_summary = submit_batch.create_batch_summary(
        batch_id, openai_batch.id, input_file
    )
    submit_batch.update_word_embedding_status(deserialized_words)
    submit_batch.add_batch_summary(batch_summary)

    assert len(words_without_embeddings) == 25
    # There are 5 receipts total (one per image)
    assert sum(len(receipt_dict) for receipt_dict in batches.values()) == 5
    # Each receipt should contain exactly 5 words
    for image_id, receipt_dict in batches.items():
        for receipt_id, words_list in receipt_dict.items():
            assert len(words_list) == 5, (
                f"Expected 5 words for image {image_id}, receipt {receipt_id}, "
                f"but got {len(words_list)}"
            )
    # The serialized words should have files per receipt
    for receipt_dict in serialized_words:
        assert receipt_dict["s3_bucket"] == s3_bucket
        assert receipt_dict["s3_key"].startswith("embeddings/")
        assert receipt_dict["s3_key"].endswith(".ndjson")
        # The file should have 5 lines, one per word
        with open(receipt_dict["ndjson_path"], "r") as f:
            assert len(f.readlines()) == 5
        # The file should exist in S3
        s3 = boto3.client("s3")
        s3.head_object(Bucket=s3_bucket, Key=receipt_dict["s3_key"])

    # Verify all uploaded keys are present in S3
    s3 = boto3.client("s3", region_name="us-east-1")
    response = s3.list_objects_v2(Bucket=s3_bucket, Prefix="embeddings/")
    uploaded_keys = [obj["Key"] for obj in response.get("Contents", [])]
    expected_keys = [d["s3_key"] for d in serialized_words]
    assert set(uploaded_keys) == set(
        expected_keys
    ), f"Expected keys {expected_keys}, but found {uploaded_keys}"
    # Verify the event is deserialized correctly
    assert len(deserialized_words) == 5
    for word in deserialized_words:
        assert word.receipt_id == 1
        assert word.image_id == "29c1d8af-035c-431f-9d80-e4053cf28a00"
    # Verify the file is formatted correctly for OpenAI batches
    with open(input_file, "r") as f:
        for line in f:
            obj = json.loads(line)
            assert set(obj.keys()) == {"custom_id", "method", "url", "body"}
            assert obj["method"] == "POST"
            assert obj["url"] == "/v1/embeddings"
            assert "input" in obj["body"] and "model" in obj["body"]
    # Verify the OpenAI client is called correctly
    fake = submit_batch.openai_client  # get the patched OpenAI client
    fake.files.create.assert_called_once()
    fake.batches.create.assert_called_once_with(
        input_file_id="fake-file-id",
        endpoint="/v1/embeddings",
        completion_window="24h",
        metadata={"model": "text-embedding-3-small"},
    )
    # Verify the batch summary is created correctly
    stored = moto_client.getBatchSummary(batch_summary.batch_id)
    assert stored.status == "PENDING"
    assert stored.word_count == 5  # or whatever your test case is
    assert set(stored.receipt_refs) == {
        (event["image_id"], event["receipt_id"])
    }
    assert stored.batch_type == "EMBEDDING"
    assert stored.openai_batch_id == "fake-batch-id"
    assert stored.submitted_at is not None
    assert stored.result_file_id == "N/A"
    assert stored.receipt_refs == [(event["image_id"], event["receipt_id"])]
    # Verify serialized NDJSON content matches the original ReceiptWord objects
    for evt in serialized_words:
        with open(evt["ndjson_path"]) as f:
            lines = [json.loads(l) for l in f]
        original = batches[evt["image_id"]][evt["receipt_id"]]
        for obj, word in zip(lines, original):
            assert obj == word.__dict__

    # Verify download_serialized_words returns the correct path
    local = submit_batch.download_serialized_words(event)
    assert local == event["ndjson_path"]

    # Verify deserialize_receipt_words returns the correct ReceiptWord objects
    expected = batches[event["image_id"]][event["receipt_id"]]
    deserialized = submit_batch.deserialize_receipt_words(local)
    assert deserialized == expected

    # Verify format_word_context_embedding returns the correct formatted input string
    sample = formatted_words[0]["body"]["input"]
    first_txt = deserialized_words[0].text
    assert sample.startswith(f"<TARGET>{first_txt}</TARGET>")
    assert sample.count("<CONTEXT>") == 1

    # Verify that the 5 Receipt's words still exist
    all_words, _ = moto_client.listReceiptWords()
    assert len(all_words) == 5 * 5, "The words still exist in the table"

    # Verify the word embedding statuses have not been updated for the 4 receipts that were not embedded
    assert (
        len(
            moto_client.listReceiptWordsByEmbeddingStatus(EmbeddingStatus.NONE)
        )
        == 5 * 4
    ), "The words that have not been embedded have the correct status"

    # Verify the word embedding statuses have been updated
    assert (
        len(
            moto_client.listReceiptWordsByEmbeddingStatus(
                EmbeddingStatus.PENDING
            )
        )
        == 5
    ), "The words that have been embedded have the correct status"
