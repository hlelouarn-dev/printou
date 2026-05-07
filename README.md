"""Tests des nouvelles features v0.2 : auto-shrink crop, sliders retouche, multi-print, tri date."""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

from printou.core import (
    FORMATS, Adjustments, AppConfig, Commande, CropRect, Database, EventInfo,
    Geometry, PhotoSource, Template, TirageDemande, TirageMultiResult, TirageService,
    scan_commande,
)
from printou.core.photo_ops import (
    apply_adjustments, largest_inscribed_rect_after_rotation, safe_crop_for_rotation,
)


passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {message}")
    else:
        failed += 1
        print(f"  ✗ {message}")


def section(title):
    print(f"\n=== {title} ===")


# ============================================================
section("Auto-shrink crop sur rotation")
# ============================================================

def test_no_rotation_full_crop():
    x, y, w, h = largest_inscribed_rect_after_rotation(900, 600, 0.0)
    check((x, y, w, h) == (0.0, 0.0, 1.0, 1.0), "rotation 0° → crop plein")


def test_small_rotation_shrinks():
    x, y, w, h = largest_inscribed_rect_after_rotation(900, 600, 3.0)
    # À 3°, on perd quelques %
    check(0.85 < w < 0.99, f"rotation 3° → w={w:.3f} (entre 0.85 et 0.99)")
    check(abs(w - h) < 0.001, "ratio préservé après rotation (w=h relatifs)")


def test_large_rotation_shrinks_more():
    x, y, w, h = largest_inscribed_rect_after_rotation(900, 600, 10.0)
    check(0.65 < w < 0.85, f"rotation 10° → w={w:.3f}")


def test_safe_crop_intersects():
    desired = CropRect(0.0, 0.0, 1.0, 1.0)
    safe = safe_crop_for_rotation(900, 600, 5.0, desired)
    check(safe.width < 1.0 and safe.height < 1.0,
          f"crop plein réduit après rot 5° → w={safe.width:.3f} h={safe.height:.3f}")


def test_safe_crop_already_within():
    """Si le crop demandé est déjà dans la zone safe, il est conservé."""
    desired = CropRect(0.3, 0.3, 0.4, 0.4)
    safe = safe_crop_for_rotation(900, 600, 3.0, desired)
    check(safe.x == 0.3 and abs(safe.width - 0.4) < 0.001,
          f"crop interne préservé après rot 3° → {safe}")


test_no_rotation_full_crop()
test_small_rotation_shrinks()
test_large_rotation_shrinks_more()
test_safe_crop_intersects()
test_safe_crop_already_within()


# ============================================================
section("Adjustments enrichies (5 paramètres)")
# ============================================================

def test_adjustments_neutral():
    adj = Adjustments()
    check(adj.is_neutral(), "adjustments par défaut sont neutres")


def test_adjustments_temperature():
    img = Image.new("RGB", (100, 100), (128, 128, 128))
    adj = Adjustments(temperature=50.0)  # chaud
    out = apply_adjustments(img, adj)
    r, g, b = out.getpixel((50, 50))
    check(r > 128 and b < 128, f"temperature +50 → R augmente ({r}), B diminue ({b})")


def test_adjustments_serialization():
    adj = Adjustments(brightness=1.2, temperature=20)
    d = adj.to_dict()
    adj2 = Adjustments.from_dict(d)
    check(adj2.brightness == 1.2 and adj2.temperature == 20,
          "sérialisation Adjustments OK")


def test_adjustments_back_compat():
    """Lire un Adjustments v0.1 (sans temperature) doit fonctionner."""
    old = {"brightness": 1.1, "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0}
    adj = Adjustments.from_dict(old)
    check(adj.temperature == 0.0, "rétro-compat avec dict v0.1 (temperature absent → 0)")


test_adjustments_neutral()
test_adjustments_temperature()
test_adjustments_serialization()
test_adjustments_back_compat()


# ============================================================
section("Tri commandes par date de création (FIFO)")
# ============================================================

def test_creation_time_in_scan():
    tmp = Path(tempfile.mkdtemp())
    try:
        cmd_dir = tmp / "local_TEST_test"
        cmd_dir.mkdir()
        sub = cmd_dir / "Tirage 20x30 cm (A4)"
        sub.mkdir()
        (sub / "IMG_0001.JPG").touch()
        cmd = scan_commande(cmd_dir)
        check(cmd.creation_time > 0, f"creation_time renseigné : {cmd.creation_time}")
    finally:
        shutil.rmtree(tmp)


def test_fifo_order():
    tmp = Path(tempfile.mkdtemp())
    try:
        # Crée 3 dossiers à 1 sec d'intervalle
        for i, name in enumerate(["A_oldest", "B_middle", "C_newest"]):
            cmd_dir = tmp / name
            cmd_dir.mkdir()
            sub = cmd_dir / "Tirage 20x30 cm (A4)"
            sub.mkdir()
            (sub / f"IMG_{i}.JPG").touch()
            time.sleep(0.05)  # petit délai pour différencier les ctimes

        commandes = []
        for d in tmp.iterdir():
            cmd = scan_commande(d)
            if cmd.photos:
                commandes.append(cmd)

        commandes.sort(key=lambda c: c.creation_time)
        names = [c.name for c in commandes]
        check(names == ["A_oldest", "B_middle", "C_newest"],
              f"tri FIFO correct : {names}")
    finally:
        shutil.rmtree(tmp)


test_creation_time_in_scan()
test_fifo_order()


# ============================================================
section("TirageService.imprimer_tous_formats (multi-format)")
# ============================================================

def test_imprimer_tous_formats():
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = AppConfig()
        cfg.commandes_root = str(tmp / "commandes")
        cfg.commandes_traitees = str(tmp / "traitees")
        cfg.logos_dir = str(tmp / "logos")
        cfg.rendered_cache_dir = str(tmp / "cache")
        cfg.exports_dir = str(tmp / "exports")
        cfg.set_hotfolder("15x23", str(tmp / "DNP_15"))
        cfg.set_hotfolder("20x30", str(tmp / "DNP_20"))
        cfg.set_event_info(EventInfo(name="Test", location="X", date="2026"))

        for d in [cfg.commandes_root, cfg.commandes_traitees, cfg.logos_dir,
                  cfg.rendered_cache_dir, cfg.exports_dir,
                  str(tmp / "DNP_15"), str(tmp / "DNP_20")]:
            Path(d).mkdir(parents=True, exist_ok=True)

        db = Database(tmp / "db.sqlite")
        service = TirageService(cfg, db)

        photo_path = tmp / "p.jpg"
        Image.new("RGB", (900, 600), (100, 150, 200)).save(photo_path, "JPEG")

        cmd_dir = Path(cfg.commandes_root) / "test_cmd"
        cmd_dir.mkdir()
        commande = Commande(folder=cmd_dir, name="test_cmd")
        photo = PhotoSource(base_name="IMG_001", representative_file=photo_path)
        photo.tirages["15x23"] = TirageDemande("15x23", 1)
        photo.tirages["20x30"] = TirageDemande("20x30", 2)

        template = Template(name="test", orientation="paysage")

        result = service.imprimer_tous_formats(
            commande, photo, template, Geometry(), Adjustments(),
        )

        check(len(result.par_format) == 2, f"2 formats traités : {len(result.par_format)}")
        check(len(result.erreurs) == 0, f"aucune erreur : {result.erreurs}")
        check(len(result.par_format["15x23"].fichiers_dispatches) == 1, "15x23 : 1 fichier")
        check(len(result.par_format["20x30"].fichiers_dispatches) == 2, "20x30 : 2 fichiers")
    finally:
        shutil.rmtree(tmp)


def test_imprimer_tous_formats_avec_erreur_partielle():
    """Si un hotfolder est manquant, les autres formats doivent quand même être traités."""
    tmp = Path(tempfile.mkdtemp())
    try:
        cfg = AppConfig()
        cfg.commandes_root = str(tmp / "commandes")
        cfg.logos_dir = str(tmp / "logos")
        cfg.rendered_cache_dir = str(tmp / "cache")
        cfg.exports_dir = str(tmp / "exports")
        cfg.set_hotfolder("20x30", str(tmp / "DNP_20"))
        # Pas de hotfolder pour 15x23 → erreur attendue
        cfg.set_event_info(EventInfo(name="T", location="X", date="2026"))

        for d in [cfg.commandes_root, cfg.logos_dir, cfg.rendered_cache_dir,
                  cfg.exports_dir, str(tmp / "DNP_20")]:
            Path(d).mkdir(parents=True, exist_ok=True)

        db = Database(tmp / "db.sqlite")
        service = TirageService(cfg, db)

        photo_path = tmp / "p.jpg"
        Image.new("RGB", (900, 600), (100, 150, 200)).save(photo_path, "JPEG")

        cmd_dir = Path(cfg.commandes_root) / "test_cmd"
        cmd_dir.mkdir()
        commande = Commande(folder=cmd_dir, name="test_cmd")
        photo = PhotoSource(base_name="IMG_001", representative_file=photo_path)
        photo.tirages["15x23"] = TirageDemande("15x23", 1)
        photo.tirages["20x30"] = TirageDemande("20x30", 2)

        result = service.imprimer_tous_formats(
            commande, photo, Template(name="t", orientation="paysage"),
            Geometry(), Adjustments(),
        )
        check(len(result.par_format) == 1 and "20x30" in result.par_format,
              "20x30 OK malgré 15x23 manquant")
        check(len(result.erreurs) == 1 and "15x23" in result.erreurs,
              "erreur sur 15x23 collectée")
    finally:
        shutil.rmtree(tmp)


test_imprimer_tous_formats()
test_imprimer_tous_formats_avec_erreur_partielle()


# ============================================================
print("\n" + "=" * 60)
print(f" v0.2 : {passed} passés, {failed} échoués")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
