"""Tests du moteur Printou v3 + services."""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PIL import Image

from printou.core import (
    FORMATS,
    Adjustments,
    AppConfig,
    Commande,
    CropRect,
    Database,
    EventInfo,
    Geometry,
    HotfolderNotConfiguredError,
    Margins,
    OrientationMismatchError,
    PhotoSource,
    RenderRequest,
    Template,
    TirageDemande,
    TirageService,
    detect_format_code,
    extract_base_name,
    fit_photo_to_zone,
    render,
    scan_commande,
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
section("Moteur V3 : marges paramétrables")
# ============================================================

def test_margins_pixels():
    margins = Margins(top=4.0, bottom=8.0, left=4.0, right=4.0)
    top, bot, left, right = margins.to_pixels(1000, 1500)
    check(top == 60 and bot == 120 and left == 40 and right == 40,
          f"Margins(4,8,4,4) sur 1000x1500 = top={top} bot={bot} left={left} right={right}")

    x, y, w, h = margins.image_zone_pixels(1000, 1500)
    check((x, y, w, h) == (40, 60, 920, 1320), f"Zone image = {(x, y, w, h)}")

test_margins_pixels()


# ============================================================
section("Algorithme : étirement intelligent + crop")
# ============================================================

def test_fit_photo_to_zone_direct():
    photo = Image.new("RGB", (1000, 666), "red")  # ratio 1.502
    # Cible ratio 1.5 : écart 0.13%, étirement direct
    result = fit_photo_to_zone(photo, 1500, 1000, max_stretch_pct=3.0)
    check(result.size == (1500, 1000), f"étirement direct → {result.size}")


def test_fit_photo_to_zone_with_crop():
    photo = Image.new("RGB", (1000, 666), "red")  # ratio 1.502
    # Cible ratio 1.6 : écart 6.5%, étirement 3% + crop
    result = fit_photo_to_zone(photo, 1600, 1000, max_stretch_pct=3.0)
    check(result.size == (1600, 1000), f"étirement + crop → {result.size}")


test_fit_photo_to_zone_direct()
test_fit_photo_to_zone_with_crop()


# ============================================================
section("Render avec template V3")
# ============================================================

def test_render_v3():
    tmp = Path(tempfile.mkdtemp())
    try:
        photo_path = tmp / "p.jpg"
        Image.new("RGB", (900, 600), (100, 150, 200)).save(photo_path, "JPEG")

        template = Template(
            name="test_v3",
            orientation="paysage",
            margins=Margins(top=4.0, bottom=8.0, left=4.0, right=4.0),
            layers=[],
        )
        req = RenderRequest(
            photo_path=photo_path,
            template=template,
            print_format=FORMATS["20x30"],
            geometry=Geometry(),
            adjustments=Adjustments(),
            placeholders={},
            logos_dir=tmp,
        )
        img = render(req)
        expected = FORMATS["20x30"].pixel_size("paysage")
        check(img.size == expected, f"rendu V3 paysage 20x30 = {img.size}")
    finally:
        shutil.rmtree(tmp)


def test_render_v3_with_layers():
    tmp = Path(tempfile.mkdtemp())
    try:
        photo_path = tmp / "p.jpg"
        Image.new("RGB", (900, 600), (100, 150, 200)).save(photo_path, "JPEG")

        # Créer un faux logo
        logo_path = tmp / "test_logo.png"
        Image.new("RGBA", (100, 100), (255, 255, 0, 200)).save(logo_path)

        template = Template(
            name="test_v3_layers",
            orientation="paysage",
            margins=Margins(top=4.0, bottom=8.0, left=4.0, right=4.0),
            layers=[
                {
                    "kind": "logo",
                    "asset_name": "test_logo.png",
                    "reference": "image",
                    "pos_x_pct": 100.0, "pos_y_pct": 100.0,
                    "anchor": "rb",
                    "width_pct": 5.0,
                    "z_order": 5,
                },
                {
                    "kind": "text",
                    "content": "{event_name}",
                    "reference": "image",
                    "pos_x_pct": 0.0, "pos_y_pct": 100.0,
                    "anchor": "lt",
                    "font_family": "Segoe UI",
                    "font_size_pct": 2.0,
                    "color": "#FFFFFF",
                    "bold": True,
                    "z_order": 5,
                },
            ],
        )
        req = RenderRequest(
            photo_path=photo_path,
            template=template,
            print_format=FORMATS["20x30"],
            geometry=Geometry(),
            adjustments=Adjustments(),
            placeholders={"event_name": "TEST"},
            logos_dir=tmp,
        )
        img = render(req)
        check(img.size == FORMATS["20x30"].pixel_size("paysage"),
              "rendu V3 avec calques OK")
    finally:
        shutil.rmtree(tmp)


test_render_v3()
test_render_v3_with_layers()


# ============================================================
section("Sérialisation/désérialisation Template")
# ============================================================

def test_template_roundtrip():
    tmp = Path(tempfile.mkdtemp())
    try:
        original = Template(
            name="roundtrip",
            orientation="paysage",
            margins=Margins(top=4.0, bottom=8.0, left=4.0, right=4.0),
            layers=[
                {"kind": "logo", "asset_name": "x.png", "pos_x_pct": 50.0,
                 "anchor": "cm", "width_pct": 10.0, "z_order": 5},
            ],
        )
        path = tmp / "t.json"
        original.to_json(path)
        loaded = Template.from_json(path)
        check(loaded.name == "roundtrip", "name préservé")
        check(loaded.orientation == "paysage", "orientation préservée")
        check(loaded.margins.top == 4.0 and loaded.margins.bottom == 8.0,
              "marges préservées")
        check(len(loaded.layers) == 1, "calques préservés")
    finally:
        shutil.rmtree(tmp)


test_template_roundtrip()


# ============================================================
section("Database SQLite")
# ============================================================

def test_database():
    tmp = Path(tempfile.mkdtemp())
    try:
        db = Database(tmp / "test.db")
        cmd_id = db.create_or_get_commande("test_cmd", tmp / "fakepath", "Jean Dupont")
        check(cmd_id > 0, f"commande créée avec id={cmd_id}")

        # Idempotence : même appel doit retourner le même id
        cmd_id2 = db.create_or_get_commande("test_cmd", tmp / "fakepath", "Jean Dupont")
        check(cmd_id == cmd_id2, "idempotence create_or_get_commande")

        photo_id = db.record_photo(
            cmd_id, "IMG_001", tmp / "img.jpg",
            Geometry().to_dict(), Adjustments().to_dict(),
        )
        check(photo_id > 0, f"photo enregistrée avec id={photo_id}")

        tirage_id = db.record_tirage(
            photo_id, "20x30", 2, "harcour_paysage",
            tmp / "rendered.jpg", tmp / "DNP", [tmp / "a.jpg", tmp / "b.jpg"],
        )
        check(tirage_id > 0, f"tirage enregistré avec id={tirage_id}")

        tirages = db.list_tirages_for_commande(cmd_id)
        check(len(tirages) == 1 and tirages[0]["quantite"] == 2,
              f"liste tirages = {len(tirages)} entrée")

        # Recherche
        results = db.search_tirages("Dupont")
        check(len(results) == 1, f"recherche 'Dupont' = {len(results)} résultat")
    finally:
        shutil.rmtree(tmp)


test_database()


# ============================================================
section("AppConfig (chargement / sauvegarde)")
# ============================================================

def test_config():
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "config.json"

        cfg = AppConfig()
        check(not cfg.is_configured(), "config vide n'est pas configurée")

        cfg.commandes_root = str(tmp)  # le tmp existe
        cfg.set_hotfolder("20x30", str(tmp))
        cfg.save(path)

        cfg2 = AppConfig.load(path)
        check(cfg2.commandes_root == str(tmp), "commandes_root persisté")
        check(cfg2.get_hotfolder("20x30") == tmp, "hotfolder persisté")
        check(cfg2.is_configured(), "config chargée est valide")
    finally:
        shutil.rmtree(tmp)


test_config()


# ============================================================
section("TirageService : pipeline d'impression complet")
# ============================================================

def test_tirage_service():
    tmp = Path(tempfile.mkdtemp())
    try:
        # Setup : config avec dossiers réels
        cfg = AppConfig()
        cfg.commandes_root = str(tmp / "commandes")
        cfg.commandes_traitees = str(tmp / "traitees")
        cfg.logos_dir = str(tmp / "logos")
        cfg.rendered_cache_dir = str(tmp / "cache")
        cfg.exports_dir = str(tmp / "exports")
        cfg.set_hotfolder("20x30", str(tmp / "DNP_20x30"))
        cfg.set_event_info(EventInfo(name="Test", location="X", date="2026"))

        for d in [cfg.commandes_root, cfg.commandes_traitees, cfg.logos_dir,
                  cfg.rendered_cache_dir, cfg.exports_dir, str(tmp / "DNP_20x30")]:
            Path(d).mkdir(parents=True, exist_ok=True)

        # DB
        db = Database(tmp / "db.sqlite")
        service = TirageService(cfg, db)

        # Photo factice
        photo_path = tmp / "fake_photo.jpg"
        Image.new("RGB", (900, 600), (100, 150, 200)).save(photo_path, "JPEG")

        # Commande factice
        cmd_dir = Path(cfg.commandes_root) / "test_cmd"
        cmd_dir.mkdir()
        commande = Commande(folder=cmd_dir, name="test_cmd")
        photo = PhotoSource(base_name="IMG_001", representative_file=photo_path)
        photo.tirages["20x30"] = TirageDemande(format_code="20x30", quantity=2)

        # Template minimal
        template = Template(name="test", orientation="paysage")

        # Geometry/Adjustments par défaut
        result = service.imprimer_tirage(
            commande, photo, "20x30", 2, template,
            Geometry(), Adjustments(),
        )
        check(len(result.fichiers_dispatches) == 2,
              f"2 fichiers dispatchés : {len(result.fichiers_dispatches)}")
        for f in result.fichiers_dispatches:
            check(f.exists(), f"fichier existe : {f.name}")

        # Vérifier la base
        commandes = db.list_commandes()
        check(len(commandes) == 1, f"1 commande en base : {len(commandes)}")

        # Test erreur si hotfolder manquant
        try:
            service.imprimer_tirage(
                commande, photo, "A2", 1, template,
                Geometry(), Adjustments(),
            )
            check(False, "expected HotfolderNotConfiguredError pour A2")
        except HotfolderNotConfiguredError:
            check(True, "HotfolderNotConfiguredError levée pour format non configuré")

    finally:
        shutil.rmtree(tmp)


test_tirage_service()


# ============================================================
section("Scan commande (matching cross-dossiers)")
# ============================================================

def test_scan():
    tmp = Path(tempfile.mkdtemp())
    try:
        cmd_dir = tmp / "local_TEST_dupont jean"
        for sub in ["Tirage 15x23 cm (A5)", "Tirage 20x30 cm (A4)", "Photo numérique Standard"]:
            (cmd_dir / sub).mkdir(parents=True)

        (cmd_dir / "Tirage 15x23 cm (A5)" / "IMG_0025.JPG").touch()
        (cmd_dir / "Tirage 20x30 cm (A4)" / "IMG_0025.JPG").touch()
        (cmd_dir / "Tirage 20x30 cm (A4)" / "IMG_0025_1.JPG").touch()
        (cmd_dir / "Tirage 20x30 cm (A4)" / "IMG_0030.JPG").touch()
        (cmd_dir / "Photo numérique Standard" / "IMG_0025.JPG").touch()

        cmd = scan_commande(cmd_dir)
        check(len(cmd.photos) == 2, f"2 photos uniques détectées : {len(cmd.photos)}")
        check(cmd.total_tirages == 4, f"4 tirages totaux : {cmd.total_tirages}")
        img25 = next(p for p in cmd.photos if p.base_name == "IMG_0025")
        check(img25.tirages["20x30"].quantity == 2, "doublons cross-dossiers OK")
        check("Photo numérique Standard" in cmd.unrecognized_subfolders,
              "photo numérique ignorée")
    finally:
        shutil.rmtree(tmp)


test_scan()


# ============================================================
section("Render réel sur photo de concours (validation visuelle V3)")
# ============================================================

def test_render_real_photo():
    photo_real = Path("/mnt/user-data/uploads/R93_9269.JPG")
    if not photo_real.exists():
        check(True, "photo réelle non disponible (skip)")
        return

    tmp = Path(tempfile.mkdtemp())
    try:
        # Template proche de la version V3 finale
        template = Template(
            name="harcour_paysage",
            orientation="paysage",
            margins=Margins(top=4.0, bottom=8.0, left=4.0, right=4.0),
            max_stretch_pct=3.0,
            layers=[
                # Texte aligné sur le bord droit de l'image, juste sous
                {
                    "kind": "text",
                    "content": "{event_name} - {event_location} - {event_date}",
                    "reference": "image",
                    "pos_x_pct": 100.0, "pos_y_pct": 100.0,
                    "anchor": "rt",
                    "font_family": "Segoe UI",
                    "font_size_pct": 2.0,
                    "color": "#FFFFFF",
                    "bold": True,
                    "z_order": 5,
                },
            ],
        )

        req = RenderRequest(
            photo_path=photo_real,
            template=template,
            print_format=FORMATS["20x30"],
            geometry=Geometry(),
            adjustments=Adjustments(),
            placeholders={
                "event_name": "10° ÉDITION DU CONCOURS HARCOUR",
                "event_location": "LE MANS",
                "event_date": "AVRIL 2026",
            },
            logos_dir=tmp,
        )

        from printou.core import render_and_save
        out = tmp / "render_real_v3.jpg"
        render_and_save(req, out)
        check(out.exists() and out.stat().st_size > 50000,
              f"rendu réel sauvé ({out.stat().st_size // 1024} ko)")

        # Copier pour visualisation
        final_out = Path("/home/claude/printou_app_validation_render.jpg")
        shutil.copy(out, final_out)
    finally:
        shutil.rmtree(tmp)


test_render_real_photo()


# ============================================================
print("\n" + "=" * 60)
print(f" Résultat : {passed} passés, {failed} échoués")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
