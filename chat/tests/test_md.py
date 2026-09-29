"""Normalisation du texte modèle avant rendu Markdown."""
from chat.md import normalize_text, render_markdown


def test_point_colle_majuscule():
    assert normalize_text("les ruines englouties.Je vérifie") == "les ruines englouties. Je vérifie"
    assert normalize_text("infos sulfure.Je creuse") == "infos sulfure. Je creuse"


def test_marque_collee_majuscule():
    assert normalize_text("comment?Voici") == "comment? Voici"
    assert normalize_text("regarde!C'est") == "regarde! C'est"


def test_titre_colle_repart_a_la_ligne():
    assert normalize_text("avant de tout illustrer.## 🌊 Le Nautilon") == (
        "avant de tout illustrer.\n\n## 🌊 Le Nautilon"
    )
    assert "## Titre" in normalize_text("## Titre")


def test_ne_touche_pas_code_url_abreviations():
    assert normalize_text("```\na.B\n```") == "```\na.B\n```"
    assert normalize_text("`a.B` et `x.Y`") == "`a.B` et `x.Y`"
    assert normalize_text("voir https://exemple.fr/A.B bas") == "voir https://exemple.fr/A.B bas"
    assert normalize_text("U.S.A et v2.0 restent") == "U.S.A et v2.0 restent"
    assert normalize_text("3.14 et fin.") == "3.14 et fin."


def test_rendu_final_corrige():
    html = render_markdown("sulfure.Je creuse.\n\nillustrer.## Le Nautilon")
    assert "sulfure. Je creuse." in html
    assert "<h2 id=" in html and "Le Nautilon" in html
    assert "##" not in html


def test_sommaire_seulement_si_3_sections():
    court = render_markdown("## Un\n\nTexte.\n\n## Deux\n\nTexte.")
    assert 'class="toc"' not in court
    long = render_markdown("## Un\n\nT.\n\n## Deux\n\nT.\n\n### Trois\n\nT.")
    assert 'class="toc"' in long
    assert long.count("<li class=\"toc-h") == 3
    assert 'href="#un"' in long and 'href="#deux"' in long and 'href="#trois"' in long


def test_ancres_slugifiees_sans_accents_et_uniques():
    html = render_markdown("## Résumé Livres\n\nT.\n\n## Résumé Livres\n\nT.\n\n## Café & Thé !\n\nT.")
    assert 'id="resume-livres"' in html
    assert 'id="resume-livres-2"' in html
    assert 'id="cafe-the"' in html
    assert 'href="#resume-livres-2"' in html


def test_titre_emoji_seul_et_xss_id():
    html = render_markdown("## 🌊\n\nT.\n\n## B\n\nT.\n\n## C\n\nT.")
    assert 'id="section-1"' in html
    html2 = render_markdown('## <img src=x onerror=alert(1)> Titré\n\nT.\n\n## B\n\nT.\n\n## C\n\nT.')
    assert "onerror" not in html2
    assert "<script>" not in html2
    assert 'id="titre"' in html2


def test_pied_sources_compte_liens():
    sans = render_markdown("Juste du texte.")
    assert "sources-note" not in sans
    un = render_markdown("Voir [doc](https://exemple.fr/a).")
    assert "Sources : 1 lien</p>" in un
    trois = render_markdown("Voir [a](https://a.fr), [b](https://b.fr) et https://c.fr/x.")
    assert "Sources : 3 liens</p>" in trois
    code = render_markdown("Exemple `https://piege.fr` sans lien.")
    assert "sources-note" not in code


def test_tableau_barre_compteur_et_csv():
    html = render_markdown("| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |")
    assert 'class="table-bar"' in html
    assert "2 lignes × 2 colonnes" in html
    assert "Copier CSV" in html and "copyTableCSV(this)" in html
    assert 'class="table-scroll"><table>' in html
    singulier = render_markdown("| A |\n| --- |\n| 1 |")
    assert "1 ligne × 1 colonne</span>" in singulier


def test_entite_nommee_liee_cliquable():
    html = render_markdown(
        "La chaîne YouTube de [Tartonpion](https://youtube.com/@tartonpion) "
        "est la super chaîne de je ne sais quoi."
    )
    assert '<a href="https://youtube.com/@tartonpion"' in html
    assert ">Tartonpion</a>" in html
    assert 'target="_blank"' in html
    assert "Sources : 1 lien</p>" in html


def test_mention_sans_url_reste_texte_seul():
    html = render_markdown("La chaîne YouTube de Tartonpion est super.")
    assert "Tartonpion" in html
    assert "<a " not in html
    assert "sources-note" not in html
    code = render_markdown("Exemple `[Tartonpion](https://youtube.com/@tartonpion)` sans lien.")
    assert "<a " not in code


def test_bulles_sources_lettre_et_tooltip():
    html = render_markdown("Lire [Le Monde](https://www.lemonde.fr/article).")
    assert 'class="src-group"' in html
    assert ">M</a>" in html  # Le Monde → M (article ignoré)
    assert 'data-tip="Le Monde"' in html  # tooltip = nom du site
    assert 'target="_blank"' in html and 'rel="noopener noreferrer nofollow"' in html
    assert "Sources : 1 lien</p>" in html  # compteur conservé


def test_bulles_dedup_par_domaine_et_url_brute():
    html = render_markdown(
        "Voir [Le Monde](https://www.lemonde.fr/a), [Encore](https://lemonde.fr/b) "
        "et https://example.com/x."
    )
    assert html.count("src-bubble src-b") == 2  # lemonde 2 fois → 1 bulle + example
    assert ">E</a>" in html  # URL brute → lettre du domaine


def test_bulles_plafond_et_overflow():
    corps = ", ".join(f"[S{i}](https://site{i}.fr/x)" for i in range(12))
    html = render_markdown(corps)
    assert html.count("src-bubble src-b") == 10
    assert ">+2</span>" in html


def test_bulles_ignorent_javascript_et_absents_sans_lien():
    html = render_markdown("Cliquez [x](javascript:alert(1)).")
    assert "src-group" not in html
    assert "javascript" not in html
    assert "src-group" not in render_markdown("Juste du texte.")
