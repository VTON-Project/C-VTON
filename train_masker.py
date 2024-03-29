from pathlib import Path

from torch.utils.data import DataLoader

from masking_model import Masker
from masking_model.data import create_masker_dataset_pair

# Config -------------------------------------------------------------------

CKPT_PATH = Path("masking_model/checkpoints")
BATCH_SIZE = 2
LEARNING_RATE = 0.0001
EPOCHS = 100

# --------------------------------------------------------------------------

train_ds, test_ds = create_masker_dataset_pair('dataset/viton/data/image',
                                               'dataset/viton/data/mask',
                                               (1024, 768),
                                               0.8, rng_seed=9750)

print("Training images:", len(train_ds))
print("Testing images:", len(test_ds), '\n')

train_dl = DataLoader(train_ds, BATCH_SIZE, True, num_workers=4, pin_memory=True)
test_dl = DataLoader(test_ds, BATCH_SIZE, False, num_workers=4, pin_memory=True)

model = Masker('PRETRAINED')
model.train_model(train_dl, test_dl, CKPT_PATH / "last.pt", CKPT_PATH / "best.pt",
                  run_json_path=CKPT_PATH / "training.json",
                  learning_rate=LEARNING_RATE, epochs=EPOCHS)
