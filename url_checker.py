#!/usr/bin/env python3
"""
url_checker.py — Vérificateur de qualité d'article (dashboard-edito)

Reçoit une URL, va chercher son contenu côté script (pas de blocage CORS,
contrairement à un JS pur dans le navigateur), et vérifie une liste de
critères qualité (section 15.2 du document de reprise).

Usage :
    python url_checker.py --fake good        # teste sur un article fake "propre"
    python url_checker.py --fake bad         # teste sur un article fake à problèmes
    python url_checker.py <url>              # teste sur une vraie URL

Étape 1 du plan (section 19) : valider la mécanique avec des données fake
avant de brancher une vraie URL.
"""

import sys
import os
import json
import argparse
from urllib.parse import urlparse
from dataclasses import dataclass, field, asdict

import requests
from bs4 import BeautifulSoup


# ----------------------------------------------------------------------
# 0bis. Internationalisation de l'affichage (rapport, IA, reality check)
#
# Ajoutée le 4 septembre 2026 sur demande de Fanny : le rapport peut
# s'afficher en français, anglais ou portugais du Brésil (menu interactif
# au lancement, ou --langue). Important : SEULS les textes d'interface
# (labels, phrases fixes, justifications générées par l'IA) sont traduits
# — le titre de l'article et les mots-clés/entités identifiés restent
# TOUJOURS dans la langue d'origine de l'article (ce sont des extraits du
# texte source, pas du texte d'interface à traduire).
# ----------------------------------------------------------------------

LANGUES_DISPONIBLES = {
    "fr": "Français",
    "en": "English",
    "pt-br": "Português (Brasil)",
}
LANGUE_DEFAUT = "fr"


def _t(cle: str, langue: str, **kwargs) -> str:
    """Traduction d'un texte d'interface. Retombe sur le français si la
    langue demandée n'a pas cette clé, puis sur la clé elle-même si elle
    est totalement absente (garde-fou pendant le développement)."""
    entree = TRADUCTIONS.get(cle)
    if not entree:
        return cle
    texte = entree.get(langue) or entree.get(LANGUE_DEFAUT) or next(iter(entree.values()))
    if kwargs:
        try:
            return texte.format(**kwargs)
        except (KeyError, IndexError):
            return texte
    return texte


TRADUCTIONS = {
    # --- En-têtes et libellés fixes du rapport --------------------------
    "titre_rapport": {"fr": "=== Vérificateur de qualité d'article ===", "en": "=== Article Quality Checker ===", "pt-br": "=== Verificador de Qualidade do Artigo ==="},
    "label_url": {"fr": "URL", "en": "URL", "pt-br": "URL"},
    "label_titre": {"fr": "Titre", "en": "Title", "pt-br": "Título"},
    "h2_detectes": {"fr": "--- Sous-titres (H2) détectés ---", "en": "--- Subheadings (H2) detected ---", "pt-br": "--- Subtítulos (H2) detectados ---"},
    "recap_a_verifier": {"fr": "--- Critères nécessitant encore une lecture IA/humaine ---", "en": "--- Criteria still needing an AI/human read ---", "pt-br": "--- Critérios que ainda precisam de leitura por IA/humano ---"},
    "aucune": {"fr": "(aucune)", "en": "(none)", "pt-br": "(nenhuma)"},
    "aucune_entite_identifiee": {"fr": "(aucune entité identifiée)", "en": "(no entity identified)", "pt-br": "(nenhuma entidade identificada)"},
    "na": {"fr": "n/d", "en": "n/a", "pt-br": "n/d"},

    # --- Labels des critères ---------------------------------------------
    "label_https": {"fr": "HTTPS", "en": "HTTPS", "pt-br": "HTTPS"},
    "label_titre_coherent": {"fr": "Titre cohérent avec le contenu", "en": "Title consistent with content", "pt-br": "Título coerente com o conteúdo"},
    "label_sourcing_present": {"fr": "Sourcing présent", "en": "Sourcing present", "pt-br": "Fontes citadas"},
    "label_liens_qualifies": {"fr": "Liens qualifiés correctement", "en": "Links correctly qualified", "pt-br": "Links corretamente qualificados"},
    "label_image_credit": {"fr": "Image avec crédit", "en": "Image with credit", "pt-br": "Imagem com crédito"},
    "label_nombre_mots": {"fr": "Nombre de mots", "en": "Word count", "pt-br": "Número de palavras"},
    "label_nombre_hyperliens": {"fr": "Nombre d'hyperliens (corps du texte)", "en": "Number of hyperlinks (body text)", "pt-br": "Número de hiperlinks (corpo do texto)"},
    "label_embeds_sociaux": {"fr": "Embeds sociaux (tweets / Instagram / Facebook)", "en": "Social embeds (tweets / Instagram / Facebook)", "pt-br": "Embeds sociais (tweets / Instagram / Facebook)"},
    "label_nombre_h2": {"fr": "Nombre de sous-titres (H2)", "en": "Number of subheadings (H2)", "pt-br": "Número de subtítulos (H2)"},
    "label_h2_mot_cle": {"fr": "Mot-clé principal présent dans un H2", "en": "Main keyword present in an H2", "pt-br": "Palavra-chave principal presente em um H2"},

    # --- Détails des critères automatisables ------------------------------
    "detail_https_ok": {"fr": "L'URL utilise https://", "en": "The URL uses https://", "pt-br": "A URL usa https://"},
    "detail_https_fail": {"fr": "L'URL n'utilise pas https://", "en": "The URL does not use https://", "pt-br": "A URL não usa https://"},
    "detail_mots_vide": {"fr": "Aucun texte détecté dans le corps de l'article.", "en": "No text detected in the article body.", "pt-br": "Nenhum texto detectado no corpo do artigo."},
    "detail_mots_court": {"fr": "{mots} mots — article court, à vérifier (souvent trop court pour Discover).", "en": "{mots} words — short article, worth checking (often too short for Discover).", "pt-br": "{mots} palavras — artigo curto, vale conferir (geralmente curto demais para o Discover)."},
    "detail_mots_ok": {"fr": "{mots} mots dans le corps du texte.", "en": "{mots} words in the body text.", "pt-br": "{mots} palavras no corpo do texto."},
    "detail_hyperliens": {"fr": "{nb} hyperlien(s) trouvé(s) dans le corps du texte.", "en": "{nb} hyperlink(s) found in the body text.", "pt-br": "{nb} hiperlink(s) encontrado(s) no corpo do texto."},
    "detail_hyperliens_suffixe_zero": {"fr": " (aucun lien de sourcing/contexte détecté — à vérifier)", "en": " (no sourcing/context link detected — worth checking)", "pt-br": " (nenhum link de fonte/contexto detectado — vale conferir)"},
    "detail_liens_qualifies_vide": {"fr": "Aucun lien à analyser.", "en": "No links to analyze.", "pt-br": "Nenhum link para analisar."},
    "detail_liens_qualifies_ok": {"fr": "Tous les liens portent un attribut rel.", "en": "All links carry a rel attribute.", "pt-br": "Todos os links têm um atributo rel."},
    "detail_liens_qualifies_warning": {"fr": "{nb_sans_rel}/{nb_total} lien(s) sans attribut rel (sponsored/ugc/nofollow) — à vérifier manuellement s'ils sont affiliés/UGC.", "en": "{nb_sans_rel}/{nb_total} link(s) without a rel attribute (sponsored/ugc/nofollow) — check manually whether they're affiliate/UGC.", "pt-br": "{nb_sans_rel}/{nb_total} link(s) sem atributo rel (sponsored/ugc/nofollow) — vale conferir manualmente se são afiliados/UGC."},
    "detail_image_aucune": {"fr": "Aucune image détectée dans le corps de l'article.", "en": "No image detected in the article body.", "pt-br": "Nenhuma imagem detectada no corpo do artigo."},
    "detail_image_credit": {"fr": "{credits}/{total} image(s) avec alt/figcaption détecté(e)(s) (heuristique — à confirmer visuellement).", "en": "{credits}/{total} image(s) with alt/figcaption detected (heuristic — confirm visually).", "pt-br": "{credits}/{total} imagem(ns) com alt/figcaption detectada(s) (heurística — confirmar visualmente)."},
    "detail_embeds_aucun": {"fr": "Aucun embed social (tweet/Instagram/Facebook) détecté.", "en": "No social embed (tweet/Instagram/Facebook) detected.", "pt-br": "Nenhum embed social (tweet/Instagram/Facebook) detectado."},
    "embed_tweet": {"fr": "{n} tweet(s)", "en": "{n} tweet(s)", "pt-br": "{n} tweet(s)"},
    "embed_instagram": {"fr": "{n} post(s) Instagram", "en": "{n} Instagram post(s)", "pt-br": "{n} post(s) do Instagram"},
    "embed_facebook": {"fr": "{n} post(s)/vidéo(s) Facebook", "en": "{n} Facebook post(s)/video(s)", "pt-br": "{n} post(s)/vídeo(s) do Facebook"},
    "detail_embeds_suffixe": {"fr": " — leurs liens internes (hashtags, mentions, t.co...) sont exclus du comptage d'hyperliens.", "en": " — their internal links (hashtags, mentions, t.co...) are excluded from the hyperlink count.", "pt-br": " — os links internos deles (hashtags, menções, t.co...) são excluídos da contagem de hiperlinks."},
    "detail_nombre_h2": {"fr": "{nb} sous-titre(s) H2 dans le corps du texte.", "en": "{nb} subheading(s) (H2) in the body text.", "pt-br": "{nb} subtítulo(s) H2 no corpo do texto."},

    # --- Détails des critères "manuel" (avant/après IA) -------------------
    "detail_manuel_defaut": {"fr": "Nécessite une lecture du texte — voir 'texte_pour_review_ia' dans le rapport.", "en": "Requires reading the text — see 'texte_pour_review_ia' in the report.", "pt-br": "Requer leitura do texto — ver 'texte_pour_review_ia' no relatório."},
    "detail_h2_mot_cle_defaut": {"fr": "Nécessite une analyse IA (--ia ou --ia-mock) pour connaître le mot-clé/les entités à chercher dans les H2.", "en": "Requires an AI analysis (--ia or --ia-mock) to know which keyword/entities to look for in the H2s.", "pt-br": "Requer uma análise de IA (--ia ou --ia-mock) para saber qual palavra-chave/quais entidades procurar nos H2."},
    "suffixe_juge_par_ia": {"fr": " (jugé par IA — vérification finale recommandée)", "en": " (AI-assessed — final check recommended)", "pt-br": " (avaliado por IA — verificação final recomendada)"},
    "suffixe_simule": {"fr": "  [⚠️ SIMULÉ]", "en": "  [⚠️ SIMULATED]", "pt-br": "  [⚠️ SIMULADO]"},

    "h2_mot_cle_non_applicable": {"fr": "Article sans vocation SEO ciblée (voir bloc Search) — critère non applicable ici.", "en": "Article with no targeted SEO intent (see Search block) — criterion not applicable here.", "pt-br": "Artigo sem intenção de SEO direcionada (ver bloco Search) — critério não aplicável aqui."},
    "h2_mot_cle_aucun_h2": {"fr": "Aucun H2 détecté dans l'article — impossible d'y placer le mot-clé ciblé.", "en": "No H2 detected in the article — impossible to place the targeted keyword there.", "pt-br": "Nenhum H2 detectado no artigo — impossível posicionar a palavra-chave ali."},
    "h2_mot_cle_trouve": {"fr": '"{entite}" (ou une variante proche) apparaît dans au moins un H2.', "en": '"{entite}" (or a close variant) appears in at least one H2.', "pt-br": '"{entite}" (ou uma variante próxima) aparece em pelo menos um H2.'},
    "h2_mot_cle_non_trouve": {"fr": "Aucune des entités identifiées ({liste}) n'apparaît dans les H2 — opportunité de structuration SEO manquée.", "en": "None of the identified entities ({liste}) appear in the H2s — missed SEO structuring opportunity.", "pt-br": "Nenhuma das entidades identificadas ({liste}) aparece nos H2 — oportunidade de estruturação SEO perdida."},

    # --- Bloc IA (Search & Discover) --------------------------------------
    "ia_titre_bloc": {"fr": "--- Analyse IA (Search & Discover){suffixe} ---", "en": "--- AI Analysis (Search & Discover){suffixe} ---", "pt-br": "--- Análise de IA (Search & Discover){suffixe} ---"},
    "ia_suffixe_simule": {"fr": "  [⚠️ SIMULÉ — aucun appel API, valeurs fictives]", "en": "  [⚠️ SIMULATED — no API call, fictitious values]", "pt-br": "  [⚠️ SIMULADO — nenhuma chamada de API, valores fictícios]"},
    "ia_search_label": {"fr": "Search (SEO) :", "en": "Search (SEO):", "pt-br": "Search (SEO):"},
    "header_search_seo": {"fr": "Search (SEO)", "en": "Search (SEO)", "pt-br": "Search (SEO)"},
    "ia_mot_cle_principal": {"fr": "  Mot-clé principal      : ", "en": "  Main keyword           : ", "pt-br": "  Palavra-chave principal : "},
    "ia_cluster_thematique": {"fr": "  Cluster thématique     : ", "en": "  Thematic cluster       : ", "pt-br": "  Cluster temático        : "},
    "ia_score_lisibilite": {"fr": "  Score lisibilité mot-clé : {score}/100", "en": "  Keyword clarity score  : {score}/100", "pt-br": "  Score de clareza da palavra-chave : {score}/100"},
    "ia_score_na": {"fr": "  Score lisibilité mot-clé : n/a", "en": "  Keyword clarity score  : n/a", "pt-br": "  Score de clareza da palavra-chave : n/a"},
    "ia_pas_de_mot_cle": {"fr": "  Pas de mot-clé de positionnement Search identifiable", "en": "  No identifiable Search positioning keyword", "pt-br": "  Nenhuma palavra-chave de posicionamento Search identificável"},
    "ia_pas_de_mot_cle_note": {"fr": "  (pas forcément un problème — l'article n'a peut-être pas cette vocation)", "en": "  (not necessarily a problem — the article may simply not be aiming for this)", "pt-br": "  (não é necessariamente um problema — o artigo pode simplesmente não ter esse objetivo)"},
    "ia_discover_label": {"fr": "Discover :", "en": "Discover:", "pt-br": "Discover:"},
    "ia_score_discover": {"fr": "  {icone} Score : {score}/100", "en": "  {icone} Score: {score}/100", "pt-br": "  {icone} Score: {score}/100"},
    "ia_points_forts": {"fr": "  Points forts  : ", "en": "  Strengths     : ", "pt-br": "  Pontos fortes : "},
    "ia_points_faibles": {"fr": "  Points faibles : ", "en": "  Weaknesses     : ", "pt-br": "  Pontos fracos  : "},

    # --- Reality check ------------------------------------------------------
    "rc_titre_bloc": {"fr": "--- Reality check (données Discover réelles, {jours} derniers jours) ---", "en": "--- Reality check (real Discover data, last {jours} days) ---", "pt-br": "--- Reality check (dados reais do Discover, últimos {jours} dias) ---"},
    "rc_entites_recherchees": {"fr": "  Entités recherchées : ", "en": "  Entities searched   : ", "pt-br": "  Entidades pesquisadas : "},
    "rc_url_deja_presente": {"fr": "  ✅ Cette URL exacte est déjà présente dans le fichier Discover (déjà sélectionnée).", "en": "  ✅ This exact URL is already present in the Discover file (already picked up).", "pt-br": "  ✅ Esta URL exata já está presente no arquivo do Discover (já selecionada)."},
    "rc_url_absente": {"fr": "  Cette URL exacte n'apparaît pas dans le fichier Discover (normal si l'article est nouveau).", "en": "  This exact URL does not appear in the Discover file (normal if the article is new).", "pt-br": "  Esta URL exata não aparece no arquivo do Discover (normal se o artigo for novo)."},
    "rc_entite_zero": {"fr": "  ⚠️  {entite} : aucun article trouvé dans Discover sur {jours} jours.", "en": "  ⚠️  {entite}: no article found in Discover over {jours} days.", "pt-br": "  ⚠️  {entite}: nenhum artigo encontrado no Discover nos últimos {jours} dias."},
    "rc_entite_zero_note": {"fr": "      → Potentiel incertain sur ce sujet précis, pas de track record récent.", "en": "      → Uncertain potential on this specific topic, no recent track record.", "pt-br": "      → Potencial incerto sobre esse tema específico, sem histórico recente."},
    "rc_entite_trouvee": {"fr": "  ✅ {entite} : {n} article(s) trouvé(s) dans Discover sur {jours} jours", "en": "  ✅ {entite}: {n} article(s) found in Discover over {jours} days", "pt-br": "  ✅ {entite}: {n} artigo(s) encontrado(s) no Discover nos últimos {jours} dias"},
    "rc_clics_moyens": {"fr": "      Clics moyens / article        : {v}", "en": "      Average clicks / article        : {v}", "pt-br": "      Cliques médios / artigo          : {v}"},
    "rc_impressions_moyennes": {"fr": "      Impressions moyennes / article : {v} ← potentiel d'audience si sélectionné", "en": "      Average impressions / article : {v} ← audience potential if selected", "pt-br": "      Impressões médias / artigo : {v} ← potencial de audiência se selecionado"},
    "rc_ctr_moyen": {"fr": "      CTR moyen du sujet             : {v}%", "en": "      Average topic CTR              : {v}%", "pt-br": "      CTR médio do tema                : {v}%"},
    "rc_exemples": {"fr": "      Exemples :", "en": "      Examples:", "pt-br": "      Exemplos:"},
    "rc_exemple_ligne": {"fr": "        • {clicks} clics / {impressions} impr. / CTR {ctr} — {url}", "en": "        • {clicks} clicks / {impressions} impr. / CTR {ctr} — {url}", "pt-br": "        • {clicks} cliques / {impressions} impr. / CTR {ctr} — {url}"},

    # --- Contexte URL (club/catégorie/type de contenu déduits de l'URL, section 0quater) ---
    "ctx_titre_bloc": {"fr": "--- Contexte déduit de l'URL ---", "en": "--- Context inferred from the URL ---", "pt-br": "--- Contexto deduzido da URL ---"},
    "ctx_club_principal": {"fr": "  Club principal          : ", "en": "  Main club               : ", "pt-br": "  Clube principal          : "},
    "ctx_club_secondaire": {"fr": "  Club(s) secondaire(s)   : ", "en": "  Secondary club(s)       : ", "pt-br": "  Clube(s) secundário(s)   : "},
    "ctx_categorie": {"fr": "  Catégorie               : ", "en": "  Category                : ", "pt-br": "  Categoria                : "},
    "ctx_type_contenu": {"fr": "  Type de contenu         : ", "en": "  Content type            : ", "pt-br": "  Tipo de conteúdo         : "},
    "ctx_evenement_competition": {"fr": "  Événement / compétition : ", "en": "  Event / competition     : ", "pt-br": "  Evento / competição      : "},
    "ctx_rubrique": {"fr": "  Rubrique (auteur)       : ", "en": "  Section (author)        : ", "pt-br": "  Coluna (autor)           : "},
    "ctx_type_sport": {"fr": "  Sport                   : ", "en": "  Sport                   : ", "pt-br": "  Esporte                  : "},
    "ctx_entites_candidates": {"fr": "  Entités candidates (slug, à valider) : ", "en": "  Candidate entities (slug, to confirm): ", "pt-br": "  Entidades candidatas (slug, a confirmar): "},
    "ctx_club_nouveau_note": {"fr": "      → club non répertorié jusqu'ici : probablement nouveau, donc pas encore de données Discover à son sujet.", "en": "      → club not seen before: likely new, so no Discover track record for it yet.", "pt-br": "      → clube ainda não listado: provavelmente novo, portanto ainda sem dados do Discover."},

    # --- Éligibilité Discover (vérifications techniques, ajoutées le 10 sept. 2026) ---
    "de_titre_bloc": {"fr": "--- Éligibilité technique Discover ---", "en": "--- Discover Technical Eligibility ---", "pt-br": "--- Elegibilidade Técnica Discover ---"},
    "label_de_max_image_preview": {"fr": "max-image-preview:large", "en": "max-image-preview:large", "pt-br": "max-image-preview:large"},
    "detail_de_mip_ok": {"fr": "Directive max-image-preview:large présente — Google peut afficher une grande image de prévisualisation dans Discover.", "en": "max-image-preview:large directive present — Google can show a large preview image in Discover.", "pt-br": "Diretiva max-image-preview:large presente — o Google pode exibir uma grande imagem de pré-visualização no Discover."},
    "detail_de_mip_fail": {"fr": "⛔ BLOQUANT — Directive max-image-preview:large ABSENTE. Sans elle, Google ne peut pas afficher d'image de prévisualisation dans Discover et l'article ne sera pratiquement jamais sélectionné.", "en": "⛔ BLOCKING — max-image-preview:large directive MISSING. Without it, Google cannot show a preview image in Discover and the article will almost never be selected.", "pt-br": "⛔ BLOQUEANTE — Diretiva max-image-preview:large AUSENTE. Sem ela, o Google não pode exibir uma imagem de pré-visualização no Discover e o artigo praticamente nunca será selecionado."},

    "label_de_noindex": {"fr": "Pas de noindex", "en": "No noindex", "pt-br": "Sem noindex"},
    "detail_de_noindex_ok": {"fr": "Aucune directive noindex détectée — la page est indexable.", "en": "No noindex directive detected — page is indexable.", "pt-br": "Nenhuma diretiva noindex detectada — a página é indexável."},
    "detail_de_noindex_fail": {"fr": "⛔ BLOQUANT — Directive noindex détectée. La page est exclue de l'index Google et ne peut JAMAIS apparaître dans Discover.", "en": "⛔ BLOCKING — noindex directive detected. The page is excluded from the Google index and can NEVER appear in Discover.", "pt-br": "⛔ BLOQUEANTE — Diretiva noindex detectada. A página está excluída do índice do Google e NUNCA pode aparecer no Discover."},

    "label_de_canonical": {"fr": "Balise canonical", "en": "Canonical tag", "pt-br": "Tag canonical"},
    "detail_de_canonical_ok": {"fr": "Canonical présent et pointe vers cette page.", "en": "Canonical present and points to this page.", "pt-br": "Canonical presente e aponta para esta página."},
    "detail_de_canonical_autre": {"fr": "⚠️ Canonical pointe vers une AUTRE URL ({cible}) — Google indexera cette cible, pas cette page.", "en": "⚠️ Canonical points to a DIFFERENT URL ({cible}) — Google will index that target, not this page.", "pt-br": "⚠️ Canonical aponta para OUTRA URL ({cible}) — o Google indexará esse destino, não esta página."},
    "detail_de_canonical_absent": {"fr": "Pas de balise canonical détectée (pas bloquant, mais recommandé).", "en": "No canonical tag detected (not blocking, but recommended).", "pt-br": "Nenhuma tag canonical detectada (não é bloqueante, mas é recomendado)."},

    "label_de_og_image": {"fr": "Image og:image", "en": "og:image", "pt-br": "Imagem og:image"},
    "detail_de_og_ok": {"fr": "og:image déclarée : {url_img}", "en": "og:image declared: {url_img}", "pt-br": "og:image declarada: {url_img}"},
    "detail_de_og_absent": {"fr": "Pas de balise og:image — Google ne trouvera peut-être pas d'image pour la carte Discover.", "en": "No og:image tag — Google might not find an image for the Discover card.", "pt-br": "Nenhuma tag og:image — o Google pode não encontrar uma imagem para o card do Discover."},

    "label_de_og_dimensions": {"fr": "Dimensions image og:image", "en": "og:image dimensions", "pt-br": "Dimensões da imagem og:image"},
    "detail_de_dim_ok": {"fr": "Image {largeur}×{hauteur}px — largeur ≥ 1200px ✓", "en": "Image {largeur}×{hauteur}px — width ≥ 1200px ✓", "pt-br": "Imagem {largeur}×{hauteur}px — largura ≥ 1200px ✓"},
    "detail_de_dim_petite": {"fr": "⚠️ Image {largeur}×{hauteur}px — largeur < 1200px. Google recommande au minimum 1200px de large pour les cartes Discover.", "en": "⚠️ Image {largeur}×{hauteur}px — width < 1200px. Google recommends at least 1200px wide for Discover cards.", "pt-br": "⚠️ Imagem {largeur}×{hauteur}px — largura < 1200px. O Google recomenda pelo menos 1200px de largura para cards do Discover."},
    "detail_de_dim_erreur": {"fr": "Impossible de vérifier les dimensions de l'image ({e}).", "en": "Could not check image dimensions ({e}).", "pt-br": "Não foi possível verificar as dimensões da imagem ({e})."},
    "detail_de_dim_pas_og": {"fr": "Pas d'og:image à vérifier.", "en": "No og:image to check.", "pt-br": "Nenhuma og:image para verificar."},

    "label_de_json_ld": {"fr": "Données structurées (JSON-LD)", "en": "Structured data (JSON-LD)", "pt-br": "Dados estruturados (JSON-LD)"},
    "detail_de_jsonld_ok": {"fr": "JSON-LD trouvé avec @type={types}.", "en": "JSON-LD found with @type={types}.", "pt-br": "JSON-LD encontrado com @type={types}."},
    "detail_de_jsonld_absent": {"fr": "Aucun JSON-LD Article/NewsArticle/BlogPosting détecté. Recommandé pour une meilleure classification dans Discover.", "en": "No Article/NewsArticle/BlogPosting JSON-LD found. Recommended for better Discover classification.", "pt-br": "Nenhum JSON-LD Article/NewsArticle/BlogPosting encontrado. Recomendado para melhor classificação no Discover."},

    "label_de_author": {"fr": "Attribution auteur", "en": "Author attribution", "pt-br": "Atribuição de autor"},
    "detail_de_author_ok": {"fr": "Auteur identifié : {auteur}", "en": "Author identified: {auteur}", "pt-br": "Autor identificado: {auteur}"},
    "detail_de_author_absent": {"fr": "Aucune attribution d'auteur détectée (ni dans le JSON-LD, ni dans les meta tags). Impact sur le signal E-E-A-T.", "en": "No author attribution detected (neither in JSON-LD nor in meta tags). Impacts E-E-A-T signal.", "pt-br": "Nenhuma atribuição de autor detectada (nem no JSON-LD, nem nas meta tags). Impacta o sinal E-E-A-T."},

    "label_de_date_pub": {"fr": "Date de publication", "en": "Publication date", "pt-br": "Data de publicação"},
    "detail_de_date_ok": {"fr": "Date de publication détectée : {date}", "en": "Publication date detected: {date}", "pt-br": "Data de publicação detectada: {date}"},
    "detail_de_date_absent": {"fr": "Aucune date de publication détectée (ni JSON-LD datePublished, ni meta article:published_time). Google ne pourra pas évaluer la fraîcheur du contenu.", "en": "No publication date detected (neither JSON-LD datePublished nor meta article:published_time). Google won't be able to assess content freshness.", "pt-br": "Nenhuma data de publicação detectada (nem JSON-LD datePublished, nem meta article:published_time). O Google não poderá avaliar a atualidade do conteúdo."},

    "label_de_viewport": {"fr": "Balise viewport (mobile)", "en": "Viewport tag (mobile)", "pt-br": "Tag viewport (mobile)"},
    "detail_de_viewport_ok": {"fr": "Balise viewport présente — page déclarée mobile-friendly.", "en": "Viewport tag present — page declared mobile-friendly.", "pt-br": "Tag viewport presente — página declarada como mobile-friendly."},
    "detail_de_viewport_absent": {"fr": "Pas de balise viewport détectée. Discover fonctionne principalement sur mobile — sans viewport, la page peut être mal affichée.", "en": "No viewport tag detected. Discover works primarily on mobile — without a viewport, the page may display poorly.", "pt-br": "Nenhuma tag viewport detectada. O Discover funciona principalmente no mobile — sem viewport, a página pode ser mal exibida."},

    # --- OpenAI (fournisseur IA alternatif, ajouté le 10 sept. 2026) ---
    "err_openai_non_installe": {"fr": "Le paquet 'openai' n'est pas installé. Lance : py -m pip install openai", "en": "The 'openai' package is not installed. Run: py -m pip install openai", "pt-br": "O pacote 'openai' não está instalado. Execute: py -m pip install openai"},
    "err_cle_openai_manquante": {"fr": "Variable d'environnement OPENAI_API_KEY manquante.", "en": "Missing OPENAI_API_KEY environment variable.", "pt-br": "Variável de ambiente OPENAI_API_KEY ausente."},
    "ia_suffixe_openai": {"fr": " [via OpenAI]", "en": " [via OpenAI]", "pt-br": " [via OpenAI]"},

    # --- Mode --test-tous-sites (une URL au hasard par site, section 15.15) ---
    "tt_ia_mock_defaut": {"fr": "(ni --ia ni --ia-mock précisé : --ia-mock utilisé par défaut pour ce test rapide)", "en": "(neither --ia nor --ia-mock given: defaulting to --ia-mock for this quick test)", "pt-br": "(nem --ia nem --ia-mock informado: usando --ia-mock por padrão para este teste rápido)"},
    "tt_aucun_site": {"fr": "Aucun site configuré dans sites_discover.json — rien à tester.", "en": "No site configured in sites_discover.json — nothing to test.", "pt-br": "Nenhum site configurado em sites_discover.json — nada para testar."},
    "tt_pas_de_csv": {"fr": "  ⚠️  Pas de CSV Discover configuré pour ce site — ignoré.\n", "en": "  ⚠️  No Discover CSV configured for this site — skipped.\n", "pt-br": "  ⚠️  Nenhum CSV do Discover configurado para este site — ignorado.\n"},
    "tt_csv_erreur": {"fr": "  ❌ Impossible de charger le CSV Discover : {e}\n", "en": "  ❌ Could not load the Discover CSV: {e}\n", "pt-br": "  ❌ Não foi possível carregar o CSV do Discover: {e}\n"},
    "tt_csv_vide": {"fr": "  ⚠️  Aucune URL trouvée dans ce CSV.\n", "en": "  ⚠️  No URL found in this CSV.\n", "pt-br": "  ⚠️  Nenhuma URL encontrada neste CSV.\n"},
    "tt_url_choisie": {"fr": "  URL testée (piochée au hasard dans le CSV Discover) : {url}\n", "en": "  URL tested (picked at random from the Discover CSV): {url}\n", "pt-br": "  URL testada (escolhida ao acaso no CSV do Discover): {url}\n"},
    "tt_fetch_erreur": {"fr": "  ❌ Impossible de récupérer la page ({e}).\n", "en": "  ❌ Could not fetch the page ({e}).\n", "pt-br": "  ❌ Não foi possível obter a página ({e}).\n"},
    "tt_analyse_erreur": {"fr": "  ❌ Erreur pendant l'analyse : {e}\n", "en": "  ❌ Error during analysis: {e}\n", "pt-br": "  ❌ Erro durante a análise: {e}\n"},
    "tt_recap_titre": {"fr": "RÉCAPITULATIF", "en": "SUMMARY", "pt-br": "RESUMO"},

    # --- Résumé générique Discover (90j), section 0quater/16 ---------------
    "dr_titre_bloc": {"fr": "--- Résumé Discover ({jours} derniers jours) ---", "en": "--- Discover summary (last {jours} days) ---", "pt-br": "--- Resumo do Discover (últimos {jours} dias) ---"},
    "dr_nb_urls": {"fr": "  {n} URLs sur Discover", "en": "  {n} URLs on Discover", "pt-br": "  {n} URLs no Discover"},
    "dr_impressions": {"fr": "  Impressions moyennes / URL : {v}", "en": "  Average impressions / URL  : {v}", "pt-br": "  Impressões médias / URL    : {v}"},
    "dr_clics_ctr": {"fr": "  Clics moyens / URL : {clics}   |   CTR moyen : {ctr}", "en": "  Average clicks / URL: {clics}   |   Average CTR: {ctr}", "pt-br": "  Cliques médios / URL: {clics}   |   CTR médio: {ctr}"},
    "dr_sujets": {"fr": "  Sujets sélectionnés : {sujets}", "en": "  Selected topics : {sujets}", "pt-br": "  Temas selecionados : {sujets}"},

    # --- Mode idée (--titre) ------------------------------------------------
    "idee_titre_bloc": {"fr": "=== Test d'idée d'article (titre pas encore publié) ===", "en": "=== Article idea test (title not yet published) ===", "pt-br": "=== Teste de ideia de artigo (título ainda não publicado) ==="},
    "idee_titre_envisage": {"fr": "Titre envisagé : {titre}", "en": "Prospective title: {titre}", "pt-br": "Título cogitado: {titre}"},
    "idee_pas_de_mot_cle": {"fr": "  Pas de mot-clé de positionnement Search clairement identifiable depuis le titre seul.", "en": "  No Search positioning keyword clearly identifiable from the title alone.", "pt-br": "  Nenhuma palavra-chave de posicionamento Search claramente identificável apenas pelo título."},
    "idee_entites_identifiees": {"fr": "  Entités identifiées    : ", "en": "  Entities identified    : ", "pt-br": "  Entidades identificadas : "},

    # --- Erreurs affichées dans le rapport (bloc IA / reality check) -------
    "err_anthropic_non_installe": {"fr": "Le paquet 'anthropic' n'est pas installé. Lance : py -m pip install -r requirements.txt", "en": "The 'anthropic' package is not installed. Run: py -m pip install -r requirements.txt", "pt-br": "O pacote 'anthropic' não está instalado. Execute: py -m pip install -r requirements.txt"},
    "err_cle_api_manquante": {"fr": "Variable d'environnement ANTHROPIC_API_KEY manquante. Voir section 15.6 du document de reprise pour la configurer.", "en": "Missing ANTHROPIC_API_KEY environment variable. See section 15.6 of the handover doc to configure it.", "pt-br": "Variável de ambiente ANTHROPIC_API_KEY ausente. Veja a seção 15.6 do documento de retomada para configurá-la."},
    "err_appel_api": {"fr": "Erreur d'appel API : {e}", "en": "API call error: {e}", "pt-br": "Erro na chamada da API: {e}"},
    "err_reponse_inattendue": {"fr": "Réponse inattendue de l'API (pas de tool_use trouvé).", "en": "Unexpected API response (no tool_use found).", "pt-br": "Resposta inesperada da API (nenhum tool_use encontrado)."},
    "err_rc_besoin_ia": {"fr": "Le reality check nécessite d'abord une analyse IA (--ia ou --ia-mock) avec des entités identifiées (entites_principales).", "en": "The reality check first requires an AI analysis (--ia or --ia-mock) with identified entities (entites_principales).", "pt-br": "O reality check exige primeiro uma análise de IA (--ia ou --ia-mock) com entidades identificadas (entites_principales)."},
    "err_csv_manquant": {"fr": "URL du CSV Discover manquante. Passe --site <cle> (voir --list-sites), --discover-csv <url>, ou configure la variable d'environnement DISCOVER_CSV_URL.", "en": "Missing Discover CSV URL. Pass --site <key> (see --list-sites), --discover-csv <url>, or set the DISCOVER_CSV_URL environment variable.", "pt-br": "URL do CSV do Discover ausente. Informe --site <chave> (veja --list-sites), --discover-csv <url>, ou configure a variável de ambiente DISCOVER_CSV_URL."},
    "err_csv_chargement": {"fr": "Erreur de chargement du CSV Discover : {e}", "en": "Error loading the Discover CSV: {e}", "pt-br": "Erro ao carregar o CSV do Discover: {e}"},
    "err_titre_besoin_ia": {"fr": "Le mode --titre nécessite --ia ou --ia-mock pour identifier le mot-clé/les entités.", "en": "The --titre mode requires --ia or --ia-mock to identify the keyword/entities.", "pt-br": "O modo --titre exige --ia ou --ia-mock para identificar a palavra-chave/as entidades."},
    "err_aucune_entite_titre": {"fr": "Aucune entité identifiée depuis ce titre — impossible de chercher dans Discover.", "en": "No entity identified from this title — impossible to search in Discover.", "pt-br": "Nenhuma entidade identificada a partir deste título — impossível pesquisar no Discover."},

    # --- Simulation IA (--ia-mock) : justifications/commentaires canned -----
    "mock_justification_search": {"fr": "[SIMULÉ — aucun appel API] Exemple de justification pour tester l'affichage et la mise en page.", "en": "[SIMULATED — no API call] Example justification to test the display and layout.", "pt-br": "[SIMULADO — nenhuma chamada de API] Exemplo de justificativa para testar a exibição e o layout."},
    "mock_justification_titre_seul": {"fr": "[SIMULÉ — mode idée, titre seul] Estimation basée uniquement sur le titre, pas de texte complet à lire.", "en": "[SIMULATED — idea mode, title only] Estimate based only on the title, no full text to read.", "pt-br": "[SIMULADO — modo ideia, apenas o título] Estimativa baseada somente no título, sem texto completo para ler."},
    "mock_point_fort_1": {"fr": "Sourcing identifiable dans le texte", "en": "Identifiable sourcing in the text", "pt-br": "Fontes identificáveis no texto"},
    "mock_point_fort_2": {"fr": "Pas de tournure clickbait évidente dans le titre", "en": "No obvious clickbait phrasing in the title", "pt-br": "Nenhuma formulação clickbait evidente no título"},
    "mock_point_faible_1": {"fr": "Statut de l'auteur non vérifiable automatiquement (à confirmer)", "en": "Author status not automatically verifiable (to confirm)", "pt-br": "Status do autor não verificável automaticamente (a confirmar)"},
    "mock_commentaire": {"fr": "[SIMULÉ — aucun appel API] Exemple de commentaire.", "en": "[SIMULATED — no API call] Example comment.", "pt-br": "[SIMULADO — nenhuma chamada de API] Exemplo de comentário."},
    "mock_cluster_mercato": {"fr": "mercato", "en": "transfer news", "pt-br": "mercado da bola"},
    "mock_cluster_resultats": {"fr": "résultats de match", "en": "match results", "pt-br": "resultados de partida"},
    "mock_cluster_general": {"fr": "actualité générale", "en": "general news", "pt-br": "notícias gerais"},

    # --- Menus interactifs (site, langue) ------------------------------------
    "menu_langue_intro": {"fr": "Langue d'affichage du rapport / Report display language / Idioma de exibição do relatório :", "en": "Langue d'affichage du rapport / Report display language / Idioma de exibição do relatório :", "pt-br": "Langue d'affichage du rapport / Report display language / Idioma de exibição do relatório :"},
    "menu_langue_choix": {"fr": "Choix (1-{n}, Entrée = français) : ", "en": "Choix (1-{n}, Entrée = français) : ", "pt-br": "Choix (1-{n}, Entrée = français) : "},
}


# ----------------------------------------------------------------------
# 1. Récupération du contenu
# ----------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 dashboard-edito-checker/1.0"
)


def fetch_html(url: str, timeout: int = 15) -> bytes:
    """Va chercher le HTML d'une page. Tourne côté script → pas de CORS.
    On renvoie les bytes bruts (pas resp.text) : BeautifulSoup détecte
    l'encodage lui-même via UnicodeDammit, ce qui est plus fiable que la
    devinette de `requests` quand le Content-Type ne précise pas le charset
    (source du bug d'accents mal décodés, ex: "BarÃ§a" au lieu de "Barça")."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.content


# ----------------------------------------------------------------------
# 2. Extraction : on isole le corps de l'article autant que possible
#    (balise <article>, sinon le plus gros bloc de texte trouvé), PUIS on
#    ne garde que les <p> qui font partie du texte courant — pas les
#    widgets "articles similaires", partage, tags, commentaires, etc.
#    qui sont très souvent imbriqués DANS <article> par les thèmes
#    WordPress (d'où le comptage de liens gonflé sans ce filtre).
# ----------------------------------------------------------------------

# mots-clés de classe/id qui trahissent un widget hors texte courant
EXCLUDE_KEYWORDS = [
    "related", "similaire", "similaires", "a-lire-aussi-box", "lire-aussi-box",
    "partager", "share", "sharing", "social",
    "widget", "sidebar", "comment", "disqus",
    "tag", "tags", "breadcrumb",
    "author-box", "author-bio", "bio-author",
    "newsletter", "advert", "sponso", "pub-", "banner",
    "recommand", "yarpp", "jp-relatedposts", "post-nav", "nav-links",
]


def _is_excluded(el, boundary) -> bool:
    """Remonte les parents de `el` jusqu'à `boundary` (exclu) et renvoie True
    si l'un d'eux a une classe/id qui ressemble à un widget hors texte courant."""
    node = el.parent
    while node is not None and node is not boundary:
        if hasattr(node, "get"):
            classes = " ".join(node.get("class", []) or []).lower()
            idattr = (node.get("id") or "").lower()
            combined = f"{classes} {idattr}"
            if any(k in combined for k in EXCLUDE_KEYWORDS):
                return True
        node = node.parent
    return False


def extract_article_body(soup: BeautifulSoup):
    # 1) on préfère un conteneur au nom explicite de contenu d'article
    #    (classes classiques WordPress/thèmes d'actu)
    content_keywords = (
        "entry-content", "post-content", "article-content", "article-body",
        "post-body", "td-post-content", "single-content", "content-single",
    )
    for tag in soup.find_all(["div", "section"], class_=True):
        classes = " ".join(tag.get("class", []) or []).lower()
        if any(k in classes for k in content_keywords):
            return tag

    # 2) sinon la balise <article>
    candidate = soup.find("article")
    if candidate is not None:
        return candidate

    # 3) sinon, fallback : le <div>/<main> avec le plus de texte
    blocks = soup.find_all(["main", "div", "section"])
    return max(
        blocks,
        key=lambda b: len(b.get_text(strip=True)),
        default=soup.body or soup,
    )


def extract_core_paragraphs(body):
    """Les <p> du corps qui ne sont pas dans un widget exclu (voir
    EXCLUDE_KEYWORDS). C'est ce sous-ensemble qui sert de référence pour le
    nombre de mots et le nombre d'hyperliens "dans le corps du texte"."""
    return [p for p in body.find_all("p") if not _is_excluded(p, body)]


def extract_core_h2(body):
    """Les <h2> du corps qui ne sont pas dans un widget exclu (mêmes règles
    que extract_core_paragraphs) — pour ne pas compter les titres de blocs
    "Articles similaires", newsletter, etc. comme des sous-titres de
    l'article lui-même."""
    return [h for h in body.find_all("h2") if not _is_excluded(h, body)]


def find_social_embeds(body):
    """Détecte les embeds Twitter/X, Instagram, Facebook dans le corps de
    l'article (blockquote.twitter-tweet, blockquote.instagram-media,
    div.fb-post/fb-video, iframes des plateformes). Renvoie une liste de
    tuples (type, élément) — type ∈ {"tweet", "instagram", "facebook"}."""
    embeds = []
    for tag in body.find_all(["blockquote", "div"], class_=True):
        classes = " ".join(tag.get("class", []) or []).lower()
        if "twitter-tweet" in classes or "twitter-video" in classes:
            embeds.append(("tweet", tag))
        elif "instagram-media" in classes:
            embeds.append(("instagram", tag))
        elif "fb-post" in classes or "fb-video" in classes:
            embeds.append(("facebook", tag))
    for iframe in body.find_all("iframe", src=True):
        src = iframe["src"].lower()
        if "platform.twitter.com" in src or (("twitter.com" in src or "x.com" in src) and "embed" in src):
            embeds.append(("tweet", iframe))
        elif "instagram.com" in src:
            embeds.append(("instagram", iframe))
        elif "facebook.com/plugins" in src:
            embeds.append(("facebook", iframe))
    return embeds


def remove_social_embeds(body, embeds):
    """Retire du DOM les embeds détectés (in-place) : leurs liens
    techniques (hashtags, mentions, t.co...) et leur texte ne doivent pas
    polluer le nombre de mots / d'hyperliens du corps du texte. On les
    compte à part (voir check_embeds_sociaux)."""
    for _, el in embeds:
        el.decompose()


# ----------------------------------------------------------------------
# 3. Résultat d'un critère
# ----------------------------------------------------------------------

@dataclass
class Critere:
    cle: str
    label: str
    type: str  # "automatisable" | "manuel" (nécessite lecture / comparaison)
    statut: str  # "ok" | "warning" | "fail" | "a_verifier"
    detail: str
    valeur: object = None


@dataclass
class RapportQualite:
    url: str
    titre: str = ""
    criteres: list = field(default_factory=list)
    criteres_discover: list = field(default_factory=list)  # éligibilité technique Discover (ajouté le 10 sept. 2026)
    texte_pour_review_ia: str = ""  # ce qu'on donnerait à Claude pour les critères "manuel"
    liens_detectes: list = field(default_factory=list)  # hrefs comptés comme "corps du texte" (debug)
    analyse_ia: dict = field(default_factory=dict)  # rempli seulement avec --ia (score Search + Discover)
    reality_check: dict = field(default_factory=dict)  # rempli seulement avec --reality-check
    contexte_url: dict = field(default_factory=dict)  # club/catégorie/type de contenu déduits de l'URL (section 0quater)
    discover_resume: dict = field(default_factory=dict)  # résumé générique Discover 90j (rempli avec --reality-check)

    def to_dict(self):
        d = asdict(self)
        return d


# ----------------------------------------------------------------------
# 4. Les critères
# ----------------------------------------------------------------------

def check_https(url: str, langue: str = LANGUE_DEFAUT) -> Critere:
    ok = urlparse(url).scheme == "https"
    return Critere(
        cle="https",
        label=_t("label_https", langue),
        type="automatisable",
        statut="ok" if ok else "fail",
        detail=_t("detail_https_ok", langue) if ok else _t("detail_https_fail", langue),
        valeur=ok,
    )


def check_word_count(body_text: str, langue: str = LANGUE_DEFAUT) -> Critere:
    mots = len(body_text.split())
    if mots == 0:
        statut = "fail"
        detail = _t("detail_mots_vide", langue)
    elif mots < 150:
        statut = "warning"
        detail = _t("detail_mots_court", langue, mots=mots)
    else:
        statut = "ok"
        detail = _t("detail_mots_ok", langue, mots=mots)
    return Critere(
        cle="nombre_mots",
        label=_t("label_nombre_mots", langue),
        type="automatisable",
        statut=statut,
        detail=detail,
        valeur=mots,
    )


def check_hyperlinks(liens, langue: str = LANGUE_DEFAUT) -> Critere:
    """Nombre d'hyperliens (balises <a href>) présents dans le corps du texte
    (liens déjà filtrés — voir extract_core_paragraphs)."""
    nb = len(liens)
    detail = _t("detail_hyperliens", langue, nb=nb)
    statut = "ok" if nb > 0 else "warning"
    if nb == 0:
        detail += _t("detail_hyperliens_suffixe_zero", langue)
    return Critere(
        cle="nombre_hyperliens",
        label=_t("label_nombre_hyperliens", langue),
        type="automatisable",
        statut=statut,
        detail=detail,
        valeur=nb,
    )


def check_liens_qualifies(liens, langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie la présence de rel="sponsored"/"ugc"/"nofollow" sur les liens
    qui semblent externes/affiliés (liens déjà filtrés — voir
    extract_core_paragraphs). On ne peut pas juger "correct" à 100% sans
    lecture humaine, donc on remonte un état des lieux factuel."""
    sans_rel = [a["href"] for a in liens if not a.get("rel")]
    avec_rel = {a["href"]: a.get("rel") for a in liens if a.get("rel")}
    nb_total = len(liens)
    nb_sans_rel = len(sans_rel)

    if nb_total == 0:
        statut, detail = "a_verifier", _t("detail_liens_qualifies_vide", langue)
    elif nb_sans_rel == 0:
        statut, detail = "ok", _t("detail_liens_qualifies_ok", langue)
    else:
        statut = "warning"
        detail = _t("detail_liens_qualifies_warning", langue, nb_sans_rel=nb_sans_rel, nb_total=nb_total)
    return Critere(
        cle="liens_qualifies",
        label=_t("label_liens_qualifies", langue),
        type="automatisable_partiel",
        statut=statut,
        detail=detail,
        valeur={"total": nb_total, "sans_rel": nb_sans_rel, "avec_rel": avec_rel},
    )


def check_image_credit(body, langue: str = LANGUE_DEFAUT) -> Critere:
    imgs = body.find_all("img")
    if not imgs:
        return Critere(
            cle="image_credit",
            label=_t("label_image_credit", langue),
            type="automatisable_partiel",
            statut="warning",
            detail=_t("detail_image_aucune", langue),
            valeur=0,
        )
    # heuristique : on cherche alt rempli, ou une legend/figcaption à proximité
    credits_trouves = 0
    for img in imgs:
        alt = (img.get("alt") or "").strip()
        figure = img.find_parent("figure")
        figcaption = figure.find("figcaption") if figure else None
        if alt or (figcaption and figcaption.get_text(strip=True)):
            credits_trouves += 1
    statut = "ok" if credits_trouves == len(imgs) else "warning"
    detail = _t("detail_image_credit", langue, credits=credits_trouves, total=len(imgs))
    return Critere(
        cle="image_credit",
        label=_t("label_image_credit", langue),
        type="automatisable_partiel",
        statut=statut,
        detail=detail,
        valeur={"total_images": len(imgs), "avec_credit_detecte": credits_trouves},
    )


def check_embeds_sociaux(embeds, langue: str = LANGUE_DEFAUT) -> Critere:
    """Compte les embeds Twitter/X, Instagram, Facebook trouvés dans
    l'article (à part du comptage de mots/liens, sur demande explicite —
    ces embeds ne sont pas "du texte" mais sont utiles à savoir compter)."""
    nb_tweet = sum(1 for t, _ in embeds if t == "tweet")
    nb_insta = sum(1 for t, _ in embeds if t == "instagram")
    nb_fb = sum(1 for t, _ in embeds if t == "facebook")
    total = len(embeds)
    if total == 0:
        detail = _t("detail_embeds_aucun", langue)
    else:
        parts = []
        if nb_tweet:
            parts.append(_t("embed_tweet", langue, n=nb_tweet))
        if nb_insta:
            parts.append(_t("embed_instagram", langue, n=nb_insta))
        if nb_fb:
            parts.append(_t("embed_facebook", langue, n=nb_fb))
        detail = ", ".join(parts) + _t("detail_embeds_suffixe", langue)
    return Critere(
        cle="embeds_sociaux",
        label=_t("label_embeds_sociaux", langue),
        type="automatisable",
        statut="ok",
        detail=detail,
        valeur={"total": total, "tweet": nb_tweet, "instagram": nb_insta, "facebook": nb_fb},
    )


def check_h2_count(h2s, langue: str = LANGUE_DEFAUT) -> Critere:
    """Compte les <h2> du corps du texte (hors widgets — voir
    extract_core_h2) et fait remonter leur texte. Purement informatif : un
    H2 n'est ni bon ni mauvais en soi, mais c'est un signal utile de
    structuration (utile pour le SEO/les featured snippets) — surtout sur
    les articles longs. `valeur.textes` sert aussi de base au critère
    "mot-clé présent dans un H2" (voir h2_mot_cle plus bas). Les textes des
    H2 eux-mêmes ne sont PAS traduits : ce sont des extraits littéraux de
    l'article, dans sa langue d'origine."""
    textes = [h.get_text(" ", strip=True) for h in h2s]
    nb = len(textes)
    detail = _t("detail_nombre_h2", langue, nb=nb)
    return Critere(
        cle="nombre_h2",
        label=_t("label_nombre_h2", langue),
        type="automatisable",
        statut="ok",
        detail=detail,
        valeur={"total": nb, "textes": textes},
    )


def critere_manuel(cle: str, label_cle: str, langue: str = LANGUE_DEFAUT, detail_cle: str = None) -> Critere:
    """Critères qui nécessitent une lecture/compréhension du texte
    (titre cohérent, sourcing). Sponsoring déclaré et pas de doublon
    interne ont été retirés de la grille le 4 septembre 2026 (demande de
    Fanny) — le premier faisait doublon avec le jugement Discover global,
    le second n'était de toute façon jamais jugeable depuis un seul
    article.
    Pas automatisables ici : on les marque 'a_verifier' et le texte
    extrait est fourni à part pour une review humaine ou IA (ex: coller
    dans une conversation Claude, cf. section 15.3 du document).
    Avec --ia/--ia-mock, les 2 sont ensuite mis à jour avec un vrai statut
    par `analyser()` — voir IA_TOOL_SCHEMA/lecture_editoriale.
    `label_cle`/`detail_cle` sont des clés de TRADUCTIONS (pas du texte brut)."""
    return Critere(
        cle=cle,
        label=_t(label_cle, langue),
        type="manuel",
        statut="a_verifier",
        detail=_t(detail_cle or "detail_manuel_defaut", langue),
    )


# ----------------------------------------------------------------------
# 4ter. Éligibilité technique Discover
#
# Ajouté le 10 septembre 2026 (roadmap Polaris, Step 2) : vérifie les
# prérequis techniques sans lesquels un article ne peut pas apparaître
# dans Google Discover, même si le contenu est excellent. Inspiré de
# l'outil DiscoReady (discoready.com) et des guidelines officielles de
# Google (developers.google.com/search/docs/appearance/google-discover).
# Ces critères sont 100% automatisables — pas d'appel IA nécessaire.
# ----------------------------------------------------------------------


def check_de_max_image_preview(soup: "BeautifulSoup", langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie la présence de la directive max-image-preview:large dans les
    meta robots/googlebot. BLOQUANT si absent — sans elle, Google ne peut
    pas afficher de grande image dans Discover."""
    for tag in soup.find_all("meta", attrs={"name": True, "content": True}):
        name = tag["name"].lower()
        if name in ("robots", "googlebot"):
            content = tag["content"].lower()
            if "max-image-preview:large" in content:
                return Critere(
                    cle="de_max_image_preview", label=_t("label_de_max_image_preview", langue),
                    type="automatisable", statut="ok",
                    detail=_t("detail_de_mip_ok", langue),
                )
    return Critere(
        cle="de_max_image_preview", label=_t("label_de_max_image_preview", langue),
        type="automatisable", statut="fail",
        detail=_t("detail_de_mip_fail", langue),
    )


def check_de_noindex(soup: "BeautifulSoup", langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie qu'aucune directive noindex n'est présente. BLOQUANT si trouvé."""
    for tag in soup.find_all("meta", attrs={"name": True, "content": True}):
        name = tag["name"].lower()
        if name in ("robots", "googlebot"):
            content = tag["content"].lower()
            if "noindex" in content:
                return Critere(
                    cle="de_noindex", label=_t("label_de_noindex", langue),
                    type="automatisable", statut="fail",
                    detail=_t("detail_de_noindex_fail", langue),
                )
    return Critere(
        cle="de_noindex", label=_t("label_de_noindex", langue),
        type="automatisable", statut="ok",
        detail=_t("detail_de_noindex_ok", langue),
    )


def check_de_canonical(soup: "BeautifulSoup", url: str, langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie que la balise canonical pointe vers cette page (ou est absente)."""
    link = soup.find("link", attrs={"rel": "canonical"})
    if link and link.get("href"):
        cible = link["href"].strip()
        # Normalise trailing slash pour la comparaison
        url_norm = url.rstrip("/")
        cible_norm = cible.rstrip("/")
        if url_norm == cible_norm or cible_norm == urlparse(url_norm).path.rstrip("/"):
            return Critere(
                cle="de_canonical", label=_t("label_de_canonical", langue),
                type="automatisable", statut="ok",
                detail=_t("detail_de_canonical_ok", langue),
            )
        else:
            return Critere(
                cle="de_canonical", label=_t("label_de_canonical", langue),
                type="automatisable", statut="warning",
                detail=_t("detail_de_canonical_autre", langue, cible=cible[:120]),
            )
    return Critere(
        cle="de_canonical", label=_t("label_de_canonical", langue),
        type="automatisable", statut="warning",
        detail=_t("detail_de_canonical_absent", langue),
    )


def check_de_og_image(soup: "BeautifulSoup", langue: str = LANGUE_DEFAUT) -> tuple:
    """Vérifie la présence d'une balise og:image. Renvoie (Critere, url_image_ou_None)."""
    tag = soup.find("meta", attrs={"property": "og:image"})
    if tag and tag.get("content", "").strip():
        url_img = tag["content"].strip()
        return (
            Critere(
                cle="de_og_image", label=_t("label_de_og_image", langue),
                type="automatisable", statut="ok",
                detail=_t("detail_de_og_ok", langue, url_img=url_img[:100]),
                valeur=url_img,
            ),
            url_img,
        )
    return (
        Critere(
            cle="de_og_image", label=_t("label_de_og_image", langue),
            type="automatisable", statut="warning",
            detail=_t("detail_de_og_absent", langue),
        ),
        None,
    )


def check_de_og_dimensions(og_image_url: str, langue: str = LANGUE_DEFAUT) -> Critere:
    """Récupère l'image og:image et vérifie ses dimensions (≥ 1200px de large).
    Nécessite Pillow (PIL) pour décoder l'image."""
    if not og_image_url:
        return Critere(
            cle="de_og_dimensions", label=_t("label_de_og_dimensions", langue),
            type="automatisable", statut="warning",
            detail=_t("detail_de_dim_pas_og", langue),
        )
    try:
        from PIL import Image
        import io
        resp = requests.get(og_image_url, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content))
        largeur, hauteur = img.size
        if largeur >= 1200:
            return Critere(
                cle="de_og_dimensions", label=_t("label_de_og_dimensions", langue),
                type="automatisable", statut="ok",
                detail=_t("detail_de_dim_ok", langue, largeur=largeur, hauteur=hauteur),
                valeur={"largeur": largeur, "hauteur": hauteur},
            )
        else:
            return Critere(
                cle="de_og_dimensions", label=_t("label_de_og_dimensions", langue),
                type="automatisable", statut="warning",
                detail=_t("detail_de_dim_petite", langue, largeur=largeur, hauteur=hauteur),
                valeur={"largeur": largeur, "hauteur": hauteur},
            )
    except Exception as e:
        return Critere(
            cle="de_og_dimensions", label=_t("label_de_og_dimensions", langue),
            type="automatisable", statut="warning",
            detail=_t("detail_de_dim_erreur", langue, e=str(e)[:100]),
        )


def check_de_json_ld(soup: "BeautifulSoup", langue: str = LANGUE_DEFAUT) -> tuple:
    """Vérifie la présence d'un bloc JSON-LD Article/NewsArticle/BlogPosting.
    Renvoie (Critere, parsed_json_ld_list) pour réutilisation par les checks suivants."""
    TYPES_ARTICLE = {"article", "newsarticle", "blogposting", "reportagenewsarticle", "liveblogposting"}
    found_types = []
    parsed_items = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        # JSON-LD peut être un objet ou un tableau (ou un @graph)
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("@graph", [data])
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            if isinstance(t, list):
                for ti in t:
                    if ti.lower() in TYPES_ARTICLE:
                        found_types.append(ti)
                        parsed_items.append(item)
            elif isinstance(t, str) and t.lower() in TYPES_ARTICLE:
                found_types.append(t)
                parsed_items.append(item)

    if found_types:
        return (
            Critere(
                cle="de_json_ld", label=_t("label_de_json_ld", langue),
                type="automatisable", statut="ok",
                detail=_t("detail_de_jsonld_ok", langue, types=", ".join(set(found_types))),
            ),
            parsed_items,
        )
    return (
        Critere(
            cle="de_json_ld", label=_t("label_de_json_ld", langue),
            type="automatisable", statut="warning",
            detail=_t("detail_de_jsonld_absent", langue),
        ),
        [],
    )


def check_de_author(soup: "BeautifulSoup", json_ld_items: list, langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie l'attribution d'un auteur (JSON-LD ou meta tag)."""
    # 1) JSON-LD author
    for item in json_ld_items:
        author = item.get("author")
        if author:
            if isinstance(author, dict):
                name = author.get("name", "")
            elif isinstance(author, list) and author:
                name = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
            elif isinstance(author, str):
                name = author
            else:
                name = ""
            if name.strip():
                return Critere(
                    cle="de_author", label=_t("label_de_author", langue),
                    type="automatisable", statut="ok",
                    detail=_t("detail_de_author_ok", langue, auteur=name.strip()[:80]),
                )
    # 2) Meta tag fallback
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content", "").strip():
        return Critere(
            cle="de_author", label=_t("label_de_author", langue),
            type="automatisable", statut="ok",
            detail=_t("detail_de_author_ok", langue, auteur=meta_author["content"].strip()[:80]),
        )
    return Critere(
        cle="de_author", label=_t("label_de_author", langue),
        type="automatisable", statut="warning",
        detail=_t("detail_de_author_absent", langue),
    )


def check_de_publication_date(soup: "BeautifulSoup", json_ld_items: list, langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie la présence d'une date de publication (JSON-LD ou meta OG)."""
    # 1) JSON-LD datePublished
    for item in json_ld_items:
        dp = item.get("datePublished")
        if dp and str(dp).strip():
            return Critere(
                cle="de_date_pub", label=_t("label_de_date_pub", langue),
                type="automatisable", statut="ok",
                detail=_t("detail_de_date_ok", langue, date=str(dp).strip()[:30]),
            )
    # 2) OG meta fallback
    meta_time = soup.find("meta", attrs={"property": "article:published_time"})
    if meta_time and meta_time.get("content", "").strip():
        return Critere(
            cle="de_date_pub", label=_t("label_de_date_pub", langue),
            type="automatisable", statut="ok",
            detail=_t("detail_de_date_ok", langue, date=meta_time["content"].strip()[:30]),
        )
    return Critere(
        cle="de_date_pub", label=_t("label_de_date_pub", langue),
        type="automatisable", statut="warning",
        detail=_t("detail_de_date_absent", langue),
    )


def check_de_viewport(soup: "BeautifulSoup", langue: str = LANGUE_DEFAUT) -> Critere:
    """Vérifie la présence d'une balise viewport (signal mobile-friendly)."""
    vp = soup.find("meta", attrs={"name": "viewport"})
    if vp:
        return Critere(
            cle="de_viewport", label=_t("label_de_viewport", langue),
            type="automatisable", statut="ok",
            detail=_t("detail_de_viewport_ok", langue),
        )
    return Critere(
        cle="de_viewport", label=_t("label_de_viewport", langue),
        type="automatisable", statut="warning",
        detail=_t("detail_de_viewport_absent", langue),
    )


def verifier_eligibilite_discover(soup: "BeautifulSoup", url: str, langue: str = LANGUE_DEFAUT) -> list:
    """Lance toutes les vérifications d'éligibilité technique Discover et
    renvoie la liste de Critere correspondante. Appelé par analyser()."""
    criteres = []

    # Tier 1 — Hard blockers
    criteres.append(check_de_max_image_preview(soup, langue))
    criteres.append(check_de_noindex(soup, langue))
    criteres.append(check_de_canonical(soup, url, langue))

    # Tier 2 — Image eligibility
    og_crit, og_url = check_de_og_image(soup, langue)
    criteres.append(og_crit)
    criteres.append(check_de_og_dimensions(og_url, langue))

    # Tier 3 — Structured data & meta signals
    jsonld_crit, jsonld_items = check_de_json_ld(soup, langue)
    criteres.append(jsonld_crit)
    criteres.append(check_de_author(soup, jsonld_items, langue))
    criteres.append(check_de_publication_date(soup, jsonld_items, langue))
    criteres.append(check_de_viewport(soup, langue))

    return criteres


# ----------------------------------------------------------------------
# 4bis. Analyse IA — Search (SEO) & Discover (score /100)
#
# Optionnelle (flag --ia), car elle appelle l'API Claude (coût + clé API
# nécessaire). Nécessite la variable d'environnement ANTHROPIC_API_KEY —
# à créer sur console.anthropic.com (compte séparé de l'abonnement Claude
# perso) et à définir comme variable d'environnement Windows durable
# (PAS dans un fichier commité sur GitHub, le dépôt est public !).
# ----------------------------------------------------------------------

try:
    import anthropic
    ANTHROPIC_DISPONIBLE = True
except ImportError:
    ANTHROPIC_DISPONIBLE = False

try:
    import openai as _openai_module
    OPENAI_DISPONIBLE = True
except ImportError:
    OPENAI_DISPONIBLE = False

# Sonnet suffit pour ce type d'analyse (classification + scoring sur un seul
# article) — pas besoin d'Opus, qui est plutôt utile pour des décisions
# d'architecture complexes (voir Annexe B du document de reprise).
IA_MODEL = "claude-sonnet-5"
OPENAI_MODEL = "gpt-4o"  # modèle OpenAI pour l'analyse IA (alternative à Claude)

IA_TOOL_SCHEMA = {
    "name": "rapport_ia",
    "description": "Rapport d'analyse Search (SEO) et Discover pour un article",
    "input_schema": {
        "type": "object",
        "properties": {
            "search": {
                "type": "object",
                "properties": {
                    "positionnable_seo": {
                        "type": "boolean",
                        "description": "L'article a-t-il une vocation de positionnement Search sur un mot-clé précis ? (false si c'est une actu chaude sans intention de recherche particulière)",
                    },
                    "mot_cle_principal": {
                        "type": ["string", "null"],
                        "description": (
                            "Le mot-clé (ou expression de recherche courte) qu'un lecteur taperait "
                            "réellement dans Google — PAS le titre de l'article recopié ou raccourci. "
                            "Basé sur les entités concrètes de l'article (joueur/club/action), ex: "
                            "'Djylian N'Guessan Liverpool transfert', pas 'Mercato Brest, ASSE : "
                            "Djylian N'Guessan finalement transféré à Liverpool avec un avenir "
                            "indécis'. Null si aucun ne ressort clairement."
                        ),
                    },
                    "cluster_thematique": {
                        "type": ["string", "null"],
                        "description": (
                            "Le cluster thématique auquel l'article se rattache. DOIT être spécifique "
                            "— jamais un seul mot générique isolé comme 'mercato' ou 'résultats' tout "
                            "seul, qui ne dit rien de particulier à cet article. Combine le type de "
                            "sujet ET les entités clés (ex: 'mercato Brest / ASSE', 'résultats Ligue 1 "
                            "- Rennes'). Null si aucun ne ressort clairement."
                        ),
                    },
                    "score_lisibilite_mot_cle": {
                        "type": ["integer", "null"],
                        "description": "0-100 : clarté avec laquelle le mot-clé/l'intention de recherche est ciblé (titre, intro, structure) — PAS un jugement qualité, juste la clarté du ciblage. null si aucun mot-clé identifiable.",
                    },
                    "justification": {"type": "string", "description": "2-3 phrases expliquant le raisonnement"},
                },
                "required": ["positionnable_seo", "mot_cle_principal", "cluster_thematique", "score_lisibilite_mot_cle", "justification"],
            },
            "discover": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "description": "0-100 : respect des guidelines Google Discover (clickbait, E-E-A-T, sourcing, sponsoring, spam policies)"},
                    "points_forts": {"type": "array", "items": {"type": "string"}},
                    "points_faibles": {"type": "array", "items": {"type": "string"}},
                    "justification": {"type": "string"},
                },
                "required": ["score", "points_forts", "points_faibles", "justification"],
            },
            "entites_principales": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "1 à 3 entités nommées PRÉCISES et concrètes qui sont le vrai sujet de "
                    "l'article : nom(s) de joueur(s), club(s), entraîneur(s) ou compétition(s), "
                    "dans l'ordre de pertinence décroissante. Toujours rempli, même si "
                    "positionnable_seo est false. Écrire le nom propre exact (ex: 'Djaoui Cissé', "
                    "'Stade Rennais', 'Sunderland'), jamais un mot générique ou une expression "
                    "(pas 'mercato', pas 'transfert', pas 'viseur', pas de bouts de phrase). "
                    "But : retrouver ce sujet précis dans un export Discover en cherchant ce nom "
                    "dans les URLs — donc un mot au sens flou ou trop commun ne sert à rien ici."
                ),
            },
            "lecture_editoriale": {
                "type": "object",
                "description": (
                    "Lecture éditoriale de 2 critères qualité qui nécessitent de comprendre le "
                    "texte (grille section 15.2 du document de reprise) : titre cohérent (anti- "
                    "clickbait), sourcing présent. Sponsoring déclaré et pas de doublon interne ne "
                    "font plus partie de la grille (retirés le 4 septembre 2026)."
                ),
                "properties": {
                    "titre_coherent": {
                        "type": "object",
                        "properties": {
                            "statut": {"type": "string", "enum": ["ok", "warning", "fail"]},
                            "commentaire": {"type": "string", "description": "1-2 phrases justifiant le statut"},
                        },
                        "required": ["statut", "commentaire"],
                    },
                    "sourcing_present": {
                        "type": "object",
                        "properties": {
                            "statut": {"type": "string", "enum": ["ok", "warning", "fail"]},
                            "commentaire": {"type": "string", "description": "1-2 phrases justifiant le statut"},
                        },
                        "required": ["statut", "commentaire"],
                    },
                },
                "required": ["titre_coherent", "sourcing_present"],
            },
        },
        "required": ["search", "discover", "entites_principales", "lecture_editoriale"],
    },
}

IA_SYSTEM_PROMPT_BASE = """Tu es un analyste éditorial SEO pour un réseau de sites d'actualité sportive.
On te donne le titre, l'URL et le texte d'un article. Analyse-le selon deux axes, en
répondant STRICTEMENT via l'outil rapport_ia (pas de texte libre) :

1. SEARCH (SEO classique) :
   - Détermine si l'article a une vocation de positionnement Search sur un mot-clé
     précis (par opposition à un article 100% actualité chaude sans intention de recherche
     particulière — dans ce cas positionnable_seo = false, mot_cle_principal = null,
     cluster_thematique = null, score_lisibilite_mot_cle = null : ce n'est pas un défaut,
     l'objectif n'est simplement pas le SEO).
   - Si oui, identifie le mot-clé principal ciblé (ou l'expression de recherche la plus
     probable) et le cluster thématique auquel il se rattache. Les deux doivent être
     spécifiques à CET article : mot_cle_principal n'est pas le titre recopié, c'est ce
     qu'un lecteur taperait réellement dans un moteur de recherche (généralement basé sur
     les entités concrètes — joueur/club — et l'action : transfert, blessure, résultat...).
     cluster_thematique n'est jamais un seul mot générique isolé ("mercato", "résultats")
     qui pourrait s'appliquer à des centaines d'autres articles sans rapport — il combine
     le type de sujet et les entités clés (ex: "mercato Brest / ASSE" plutôt que "mercato").
   - Donne un score_lisibilite_mot_cle de 0 à 100 : à quel point ce mot-clé (ou cette
     intention de recherche) ressort clairement dans le titre, l'intro et la structure —
     un score de clarté du ciblage, pas un jugement de qualité globale de l'article.

2. DISCOVER : donne un score de 0 à 100 sur le respect des guidelines Google Discover :
   pas de contenu trompeur/clickbait, sourcing clair, sponsoring déclaré si présent,
   E-E-A-T (auteur identifié, profondeur, autorité thématique), pas de "scaled content
   abuse". Liste les points forts et les points faibles observés.

3. ENTITÉS PRINCIPALES : identifie 1 à 3 entités nommées PRÉCISES qui sont le vrai
   sujet de l'article — un ou plusieurs noms de joueur(s), club(s), entraîneur(s) ou
   compétition(s), jamais un mot générique. Ce champ sert à retrouver ce sujet précis
   dans un export de données Google Discover en cherchant ces noms dans des URLs
   d'articles : un mot vague ("mercato", "transfert", "viseur", "match") ou un bout de
   phrase du titre n'y aidera pas, seul un nom propre reconnaissable le peut. Exemple :
   pour un titre "Mercato Rennes : Djaoui Cissé est déjà dans le viseur d'un club de
   Ligue Europa", les bonnes entités sont ["Djaoui Cissé", "Rennes"] (et pas "viseur",
   "Ligue Europa" étant trop vague ici puisqu'aucun club adverse n'est nommé). S'il y a
   plusieurs sujets pertinents qui se chevauchent (ex: un joueur ET son club, ou deux
   clubs impliqués dans un même transfert), liste-les tous (jusqu'à 3) plutôt que d'en
   choisir un seul arbitrairement.

4. LECTURE ÉDITORIALE : juge 2 critères qui nécessitent de comprendre le texte (statut
   "ok"/"warning"/"fail" + commentaire de 1-2 phrases pour chacun) :
   - titre_coherent : le titre ne promet pas plus que ce que l'article livre. "fail" =
     clickbait manifeste (le titre suggère une info que le texte ne confirme pas ou pas
     clairement).
   - sourcing_present : au moins une source identifiable pour les informations factuelles
     (média cité, communiqué officiel, citation attribuée). "fail" = aucune source pour une
     affirmation factuelle importante ; "warning" = source vague/non nommée ("selon des
     informations")."""

# Instruction de langue ajoutée dynamiquement à IA_SYSTEM_PROMPT_BASE selon la
# langue d'affichage choisie (section 0bis) : seuls les champs de texte libre
# (justification, commentaire) doivent être écrits dans la langue choisie —
# mot_cle_principal/cluster_thematique/entites_principales doivent RESTER
# dans la langue d'origine de l'article (ce sont des extraits du texte
# source, jamais traduits).
_IA_INSTRUCTION_LANGUE = {
    "fr": (
        "\n\nRéponds en français dans les champs \"justification\" et \"commentaire\", de façon "
        "factuelle et actionnable pour un rédacteur/éditeur. En revanche, les champs "
        "mot_cle_principal, cluster_thematique et entites_principales doivent rester dans la "
        "langue d'origine de l'article — ne les traduis jamais, même si l'article n'est pas en "
        "français."
    ),
    "en": (
        "\n\nWrite the \"justification\" and \"commentaire\" fields in English, factually and in "
        "a way a writer/editor can act on. However, the mot_cle_principal, cluster_thematique and "
        "entites_principales fields must stay in the article's ORIGINAL language — never "
        "translate them, even if the article is not in English."
    ),
    "pt-br": (
        "\n\nEscreva os campos \"justification\" e \"commentaire\" em português do Brasil, de forma "
        "factual e acionável para um redator/editor. Já os campos mot_cle_principal, "
        "cluster_thematique e entites_principales devem permanecer no idioma ORIGINAL do artigo — "
        "nunca os traduza, mesmo que o artigo não esteja em português."
    ),
}


def construire_system_prompt_ia(langue: str = LANGUE_DEFAUT) -> str:
    return IA_SYSTEM_PROMPT_BASE + _IA_INSTRUCTION_LANGUE.get(langue, _IA_INSTRUCTION_LANGUE[LANGUE_DEFAUT])


def analyser_ia(titre: str, url: str, texte: str, langue: str = LANGUE_DEFAUT) -> dict:
    """Appelle l'API Claude pour l'analyse Search + Discover. Renvoie soit le
    rapport structuré, soit {"erreur": "..."} si la clé API manque ou en cas
    de problème — le reste du rapport fonctionne quand même sans ce bloc.
    `langue` ne s'applique qu'aux champs de texte libre générés par l'IA
    (justification, commentaire) — voir construire_system_prompt_ia."""
    if not ANTHROPIC_DISPONIBLE:
        return {"erreur": _t("err_anthropic_non_installe", langue)}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"erreur": _t("err_cle_api_manquante", langue)}

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=IA_MODEL,
            max_tokens=1500,
            system=construire_system_prompt_ia(langue),
            tools=[IA_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "rapport_ia"},
            messages=[{
                "role": "user",
                "content": f"Titre : {titre}\nURL : {url}\n\nTexte de l'article :\n{texte[:12000]}",
            }],
        )
    except Exception as e:
        return {"erreur": _t("err_appel_api", langue, e=e)}

    for block in message.content:
        if block.type == "tool_use" and block.name == "rapport_ia":
            return block.input

    return {"erreur": _t("err_reponse_inattendue", langue)}


def analyser_ia_openai(titre: str, url: str, texte: str, langue: str = LANGUE_DEFAUT) -> dict:
    """Appelle l'API OpenAI (gpt-4o) pour l'analyse Search + Discover.
    Même schéma/prompt que analyser_ia() (Anthropic), format de requête
    différent (function calling au lieu de tool_use). Ajouté le 10 septembre
    2026 pour permettre de tester le pipeline en live avec la clé OpenAI
    déjà disponible, en attendant la validation du compte Anthropic API.
    `langue` ne s'applique qu'aux champs de texte libre générés par l'IA."""
    if not OPENAI_DISPONIBLE:
        return {"erreur": _t("err_openai_non_installe", langue)}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"erreur": _t("err_cle_openai_manquante", langue)}

    # Convertir le schéma Anthropic (input_schema) au format OpenAI (parameters)
    openai_tool = {
        "type": "function",
        "function": {
            "name": IA_TOOL_SCHEMA["name"],
            "description": IA_TOOL_SCHEMA["description"],
            "parameters": IA_TOOL_SCHEMA["input_schema"],
        },
    }

    client = _openai_module.OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": construire_system_prompt_ia(langue)},
                {"role": "user", "content": f"Titre : {titre}\nURL : {url}\n\nTexte de l'article :\n{texte[:12000]}"},
            ],
            tools=[openai_tool],
            tool_choice={"type": "function", "function": {"name": "rapport_ia"}},
            max_tokens=1500,
        )
    except Exception as e:
        return {"erreur": _t("err_appel_api", langue, e=e)}

    # Extraire le résultat du function call
    choice = response.choices[0] if response.choices else None
    if choice and choice.message and choice.message.tool_calls:
        tc = choice.message.tool_calls[0]
        if tc.function and tc.function.name == "rapport_ia":
            try:
                result = json.loads(tc.function.arguments)
                result["_via_openai"] = True
                return result
            except json.JSONDecodeError:
                return {"erreur": _t("err_reponse_inattendue", langue)}

    return {"erreur": _t("err_reponse_inattendue", langue)}


# Mots capitalisés qui ne sont JAMAIS une entité, même accolés à un autre
# mot capitalisé (utilisé par le mode --ia-mock uniquement — le vrai appel
# API raisonne, lui, sans liste figée).
_MOTS_GENERIQUES_STRICTS = {
    "Mercato", "Match", "Transfert", "Officiel", "Direct", "Exclusif",
    "Info", "Vidéo",
}

# Mots capitalisés génériques SEULS, mais qui font partie d'un vrai nom
# propre quand ils préfixent immédiatement un autre mot capitalisé (ou un
# nombre) : "Ligue Europa", "Ligue 1", "Coupe Davis"... Sans ce cas
# particulier, "Ligue Europa" se faisait couper en "Europa" tout seul, qui
# ne veut rien dire (retour de test réel du 4 septembre 2026).
_MOTS_GENERIQUES_PREFIXABLES = {"Ligue", "Coupe", "Championnat"}


def _entites_naive(titre: str, limite: int = 3) -> list:
    """Extraction heuristique d'entités nommées à partir du titre seul (pas
    de vraie compréhension du texte) : repère les suites de mots capitalisés
    (noms propres probables — joueur, club...), écarte les mots génériques
    isolés (ex: "Mercato"), mais garde un mot générique "préfixable" (ex:
    "Ligue") s'il est immédiatement suivi d'un mot capitalisé/numérique qui
    en fait un vrai nom ("Ligue Europa", "Ligue 1") plutôt que de le couper.
    Approximatif par nature — seulement pour --ia-mock ; le vrai appel API
    (--ia) fait une extraction bien plus fiable en lisant le texte complet.
    `limite` : nombre max d'entités renvoyées (les 3 premières du titre par
    défaut, cf. usage normal pour mot-clé/cluster). Passer None pour TOUTES
    les entités détectées, sans troncature — utilisé par _candidats_slug()
    pour retrouver des entités qui apparaissent plus tard dans un long
    titre clickbait (le nom du joueur n'est pas toujours dans les 3
    premiers mots capitalisés du titre)."""
    import re

    mots_bruts = titre.split()
    # Tokens nettoyés SANS supprimer les tokens devenus vides (ex: ":" isolé) :
    # un token vide doit quand même casser une suite de mots capitalisés,
    # sinon "Rennes : Djaoui Cissé" fusionnerait en une seule entité.
    mots = [re.sub(r"^[^\wÀ-ÿ]+|[^\wÀ-ÿ]+$", "", m) for m in mots_bruts]

    # Une virgule/point-virgule/deux-points COLLÉ au mot (ex: "Brest,") doit
    # aussi casser la suite, même si le mot lui-même reste capitalisé une
    # fois nettoyé — sinon "Mercato Brest, ASSE : ..." fusionnait à tort en
    # une seule entité "Brest ASSE" au lieu de deux clubs distincts (retour
    # de test réel du 4 septembre 2026).
    CASSE_APRES = ",;:!?"

    def _casse_apres(mot_brut: str) -> bool:
        return bool(mot_brut) and mot_brut[-1] in CASSE_APRES

    entites, courant = [], []

    def _flush():
        if courant:
            entites.append(" ".join(courant))
            courant.clear()

    i, n = 0, len(mots)
    while i < n:
        mot, mot_brut = mots[i], mots_bruts[i]
        if not mot:
            _flush()
            i += 1
            continue
        if mot in _MOTS_GENERIQUES_STRICTS:
            _flush()
            i += 1
            continue
        if mot in _MOTS_GENERIQUES_PREFIXABLES:
            suivant = mots[i + 1] if i + 1 < n else ""
            suivant_brut = mots_bruts[i + 1] if i + 1 < n else ""
            suivant_valide = bool(suivant) and (suivant[0].isupper() or suivant.isdigit()) and suivant not in _MOTS_GENERIQUES_STRICTS
            if suivant_valide:
                courant.extend([mot, suivant])
                if _casse_apres(suivant_brut):
                    _flush()
                i += 2
            else:
                _flush()
                i += 1
            continue
        # Sigle tout en majuscules (ASSE, PSG, OM, OL...) : en football
        # français, un sigle de club est presque toujours son propre nom
        # complet, jamais accolé à un autre mot capitalisé pour former une
        # entité plus longue — contrairement à un nom de personne ("Djylian
        # N'Guessan"). On l'isole donc systématiquement, avant ET après,
        # plutôt que de le laisser fusionner avec son voisin (retour de test
        # réel : "Brest ASSE" doit donner 2 entités, "Brest" et "ASSE", pas
        # une seule — même sans virgule entre les deux dans le titre).
        if len(mot) >= 2 and mot.isupper():
            _flush()
            entites.append(mot)
            i += 1
            continue
        if len(mot) >= 2 and mot[0].isupper():
            courant.append(mot)
            if _casse_apres(mot_brut):
                _flush()
        else:
            _flush()
        i += 1
    _flush()

    vues, resultat = set(), []
    for e in entites:
        if e.lower() not in vues:
            vues.add(e.lower())
            resultat.append(e)
    return resultat if limite is None else resultat[:limite]


def _cluster_base_simule(titre_bas: str, langue: str) -> str:
    """Choix du libellé de cluster générique côté simulation (--ia-mock) —
    contenu SYNTHÉTIQUE (pas un extrait de l'article), donc traduit comme le
    reste de l'interface, contrairement au mot-clé/aux entités réels."""
    if "mercato" in titre_bas or "transfert" in titre_bas:
        return _t("mock_cluster_mercato", langue)
    elif "résultat" in titre_bas or "score" in titre_bas:
        return _t("mock_cluster_resultats", langue)
    return _t("mock_cluster_general", langue)


def analyser_ia_mock(titre: str, url: str, texte: str, langue: str = LANGUE_DEFAUT) -> dict:
    """Résultat SIMULÉ (aucun appel API, aucun coût) — pour tester la
    mécanique et l'affichage du bloc IA pendant que le compte/crédits API
    sont en attente de validation. Valeurs plausibles mais fictives,
    marquées _simule=True pour que l'affichage le signale clairement.
    `langue` ne s'applique qu'au texte synthétique (justification, cluster
    générique) — le mot-clé/les entités restent basés sur le titre réel de
    l'article, jamais traduits."""
    mots = len(texte.split())
    titre_bas = (titre + " " + texte[:500]).lower()
    cluster_base = _cluster_base_simule(titre_bas, langue)

    # Comme pour le mot-clé réel (--ia), on évite de recopier le titre ou de
    # renvoyer un cluster trop générique tout seul ("mercato") — on s'appuie
    # sur les mêmes entités heuristiques que entites_principales (retour de
    # test réel du 4 septembre 2026 : "mercato" seul jugé trop vague).
    entites = _entites_naive(titre)
    mot_cle = ", ".join(entites) if entites else (titre[:60] if mots >= 150 else None)
    cluster = f"{cluster_base} — {' / '.join(entites[:2])}" if entites else cluster_base

    return {
        "_simule": True,
        "search": {
            "positionnable_seo": mots >= 150,
            "mot_cle_principal": mot_cle if mots >= 150 else None,
            "cluster_thematique": cluster if mots >= 150 else None,
            "score_lisibilite_mot_cle": 72 if mots >= 150 else None,
            "justification": _t("mock_justification_search", langue),
        },
        "discover": {
            "score": 78,
            "points_forts": [_t("mock_point_fort_1", langue), _t("mock_point_fort_2", langue)],
            "points_faibles": [_t("mock_point_faible_1", langue)],
            "justification": _t("mock_justification_search", langue),
        },
        # Heuristique de secours (voir _entites_naive) — en --ia réel, ces
        # entités viennent d'un vrai raisonnement de l'API sur le texte.
        "entites_principales": entites,
        # Jugement SIMULÉ (toujours "ok") — en --ia réel, l'API lit vraiment
        # le texte pour ces 2 critères plutôt que de renvoyer une valeur fixe.
        "lecture_editoriale": {
            "titre_coherent": {"statut": "ok", "commentaire": _t("mock_commentaire", langue)},
            "sourcing_present": {"statut": "ok", "commentaire": _t("mock_commentaire", langue)},
        },
    }


def analyser_ia_mock_titre(titre: str, langue: str = LANGUE_DEFAUT) -> dict:
    """Variante de analyser_ia_mock() pour le mode --titre (section 15.11 :
    tester une idée d'article avant même de l'écrire, à partir du seul
    titre envisagé — pas de corps de texte). Contrairement à
    analyser_ia_mock(), ne bloque pas positionnable_seo sur un seuil de
    mots : il n'y a pas de texte à mesurer ici, seulement un titre. Ne
    renvoie pas de bloc discover/lecture_editoriale — ces jugements n'ont
    pas de sens sans un vrai texte d'article à lire."""
    entites = _entites_naive(titre)
    titre_bas = titre.lower()
    cluster_base = _cluster_base_simule(titre_bas, langue)
    mot_cle = ", ".join(entites) if entites else None
    cluster = f"{cluster_base} — {' / '.join(entites[:2])}" if entites else None
    return {
        "_simule": True,
        "search": {
            "positionnable_seo": bool(entites),
            "mot_cle_principal": mot_cle,
            "cluster_thematique": cluster,
            "score_lisibilite_mot_cle": 65 if entites else None,
            "justification": _t("mock_justification_titre_seul", langue),
        },
        "entites_principales": entites,
    }


# ----------------------------------------------------------------------
# 4ter. Reality check — croisement avec les vraies données Discover
#       publiées en CSV depuis Google Sheets (période couverte par l'export
#       de Fanny : rolling 90 derniers jours — voir DISCOVER_JOURS_DEFAUT).
#
# Principe : le score Discover du bloc IA (4bis) est un jugement qualitatif
# ("est-ce que cet article respecte les guidelines"). Le reality check est
# un chiffre factuel : "est-ce que ce sujet a RÉELLEMENT été sélectionné par
# Discover sur ce site sur la période couverte, et avec quelle audience ?".
# Le fichier Discover (URL, clicks, impressions, CTR — pas de query, ce
# n'est pas exposé par Google pour Discover) n'a pas de colonne "sujet" :
# on rapproche donc par présence du nom de chaque entité (joueur, club...)
# dans l'URL/le slug de chaque ligne, faute de mieux tant que le registre
# éditorial classifié (section 3/4 du document) n'est pas encore branché.
#
# Historique : la première version matchait sur des mots-clés isolés issus
# du mot-clé SEO + du cluster thématique ("n'importe quel mot"), ce qui
# remontait trop de faux positifs (des mots comme "français"/"ligue"
# capturaient des articles sans rapport). Corrigé le 4 septembre 2026 sur
# retour de test réel : l'IA identifie maintenant explicitement 1 à 3
# entités nommées précises (entites_principales, voir IA_TOOL_SCHEMA), et
# le matching exige que TOUS les mots de l'entité soient présents dans
# l'URL (pas juste un seul), avec un résultat séparé par entité.
# ----------------------------------------------------------------------

DISCOVER_JOURS_DEFAUT = 90  # période réelle couverte par l'export Discover de Fanny

# Alias de colonnes acceptés (le nom exact dans le Google Sheet peut varier).
# Inclut aussi les noms bruts renvoyés par l'API Search Console quand le
# Google Sheet est alimenté directement via Apps Script sans renommage
# (ex: "rows.keys.1", "rows.clicks", "rows.impressions", "rows.ctr").
DISCOVER_COL_ALIASES = {
    "url": ["url", "landing page", "page", "adresse", "keys", "key"],
    "clicks": ["clicks", "clics", "click"],
    "impressions": ["impressions", "impr"],
    "ctr": ["ctr", "taux de clic", "click through rate"],
}

# ----------------------------------------------------------------------
# Multi-sites : mapping site -> domaine -> lien CSV Discover publié.
# Ajouté le 4 septembre 2026 sur demande de Fanny : au lieu de préciser
# --discover-csv à chaque lancement, un fichier JSON à côté du script
# (sites_discover.json) liste les sites déjà configurés. En mode URL, le
# bon CSV est choisi automatiquement selon le domaine de l'URL testée. En
# mode --titre (pas d'URL, donc pas de domaine à détecter), un menu
# interactif propose de choisir le site si plusieurs sont configurés.
# Fichier au format :
#   {
#     "topmercato": {"nom": "Topmercato", "domaine": "topmercato.com",
#                     "discover_csv": "https://docs.google.com/.../pub?..."},
#     ...
#   }
# ----------------------------------------------------------------------

SITES_CONFIG_PATH = os.environ.get(
    "SITES_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites_discover.json"),
)


def charger_sites_config(chemin: str = None) -> dict:
    """Charge le mapping site -> {nom, domaine, discover_csv} depuis le
    fichier JSON de config. Renvoie un dict vide (sans planter) si le
    fichier n'existe pas ou est invalide — le script reste utilisable
    sans cette config (avec --discover-csv à la main, comme avant)."""
    chemin = chemin or SITES_CONFIG_PATH
    if not os.path.isfile(chemin):
        return {}
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        print(f"⚠️  Impossible de lire {chemin} (JSON invalide ?) — poursuite sans config multi-sites.")
        return {}


def _domaine_normalise(domaine_ou_url: str) -> str:
    """Normalise un domaine ou une URL en nom d'hôte comparable (retire
    www., http(s)://, minuscule)."""
    if not domaine_ou_url:
        return ""
    d = domaine_ou_url.strip().lower()
    if "://" in d:
        d = urlparse(d).netloc
    if d.startswith("www."):
        d = d[4:]
    return d


def detecter_site_par_url(url: str, sites: dict) -> str:
    """Cherche, parmi les sites configurés, celui dont le domaine
    correspond à l'URL donnée. Renvoie la clé du site trouvé, ou None.
    Quand plusieurs sites partagent le même domaine (ex: Afrik-Foot
    FR/NG/ZA, tous sur afrik-foot.com), départage par préfixe de chemin
    (champ optionnel "prefixe" dans sites_discover.json, ex: "en-ng") —
    même logique que _detecter_site_categorie (section 0quater), pour que
    la sélection automatique du CSV Discover fonctionne aussi bien que la
    détection du contexte URL sur ces variantes."""
    if not url or not sites:
        return None
    domaine_url = _domaine_normalise(url)
    if not domaine_url:
        return None
    candidats_meme_domaine = {
        cle: infos for cle, infos in sites.items()
        if _domaine_normalise((infos or {}).get("domaine", "")) in (domaine_url,)
        or domaine_url.endswith("." + _domaine_normalise((infos or {}).get("domaine", "")))
        if (infos or {}).get("domaine")
    }
    if not candidats_meme_domaine:
        return None
    if len(candidats_meme_domaine) == 1:
        return next(iter(candidats_meme_domaine))
    segs = _segments_chemin(url)
    for cle, infos in candidats_meme_domaine.items():
        prefixe = (infos or {}).get("prefixe")
        if prefixe and segs and segs[0] == prefixe:
            return cle
    prefixes_connus = {(i or {}).get("prefixe") for i in candidats_meme_domaine.values() if (i or {}).get("prefixe")}
    for cle, infos in candidats_meme_domaine.items():
        if not (infos or {}).get("prefixe") and not (segs and segs[0] in prefixes_connus):
            return cle
    return None


def afficher_menu_sites(sites: dict):
    """Affiche la liste des sites configurés (utilisé par --list-sites et
    par le menu interactif)."""
    if not sites:
        print(f"Aucun site configuré dans {SITES_CONFIG_PATH}.")
        return
    print(f"Sites configurés ({SITES_CONFIG_PATH}) :")
    for i, (cle, infos) in enumerate(sites.items(), start=1):
        nom = (infos or {}).get("nom", cle)
        domaine = (infos or {}).get("domaine", "?")
        print(f"  {i}. {cle} — {nom} ({domaine})")


def choisir_langue_interactif() -> str:
    """Menu interactif (input()) pour choisir la langue d'affichage du
    rapport (fr/en/pt-br) — affiché par défaut au lancement (section
    0bis/15.13) sauf si --langue ou LANGUE_AFFICHAGE est déjà précisé, ou
    hors d'un terminal interactif (auto-fallback sur LANGUE_DEFAUT). Le
    menu lui-même reste trilingue (fr/en/pt-br dans la même ligne) pour
    rester compréhensible avant même que la langue soit choisie."""
    if not sys.stdin.isatty():
        return LANGUE_DEFAUT
    cles = list(LANGUES_DISPONIBLES.keys())
    print(f"\n{TRADUCTIONS['menu_langue_intro']['fr']}")
    for i, cle in enumerate(cles, start=1):
        print(f"  {i}. {LANGUES_DISPONIBLES[cle]} ({cle})")
    try:
        choix = input(f"Choix (1-{len(cles)}, Entrée = {LANGUES_DISPONIBLES[LANGUE_DEFAUT]}) : ").strip()
    except (EOFError, KeyboardInterrupt):
        return LANGUE_DEFAUT
    if not choix:
        return LANGUE_DEFAUT
    if choix.isdigit() and 1 <= int(choix) <= len(cles):
        return cles[int(choix) - 1]
    if choix.lower() in LANGUES_DISPONIBLES:
        return choix.lower()
    print("Choix non reconnu — français par défaut / defaulting to French / padrão: francês.")
    return LANGUE_DEFAUT


def resoudre_langue(langue_cli: str = None) -> str:
    """Priorité : --langue (CLI) > variable d'environnement LANGUE_AFFICHAGE
    > menu interactif (terminal seulement) > LANGUE_DEFAUT (fr)."""
    if langue_cli:
        return langue_cli
    env_langue = os.environ.get("LANGUE_AFFICHAGE")
    if env_langue and env_langue in LANGUES_DISPONIBLES:
        return env_langue
    return choisir_langue_interactif()


def choisir_site_interactif(sites: dict) -> str:
    """Menu interactif (input()) pour choisir un site parmi ceux
    configurés — utilisé en mode --titre quand aucun --site/--discover-csv
    n'a été précisé. Renvoie la clé choisie, ou None si annulé/impossible
    (pas de terminal interactif, ou aucun site configuré)."""
    if not sites:
        return None
    if not sys.stdin.isatty():
        return None
    cles = list(sites.keys())
    print("\nPour quel site veux-tu tester ce titre ?")
    for i, cle in enumerate(cles, start=1):
        infos = sites[cle] or {}
        print(f"  {i}. {infos.get('nom', cle)} ({infos.get('domaine', '?')})")
    try:
        choix = input(f"Choix (1-{len(cles)}, Entrée pour annuler) : ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choix:
        return None
    if choix.isdigit() and 1 <= int(choix) <= len(cles):
        return cles[int(choix) - 1]
    # Autorise aussi de taper directement la clé du site.
    if choix in sites:
        return choix
    print("Choix non reconnu, aucun site sélectionné.")
    return None


def resoudre_discover_csv_url(sites: dict, discover_csv: str = None, site: str = None, url_article: str = None) -> tuple:
    """Détermine quelle URL de CSV Discover utiliser, par ordre de
    priorité : --discover-csv (override explicite) > --site (choisi à la
    main) > détection automatique par domaine (si une URL d'article est
    fournie) > variable d'environnement DISCOVER_CSV_URL > menu interactif
    (uniquement si aucune URL d'article, ex: mode --titre, et plusieurs
    sites configurés) . Renvoie (url_csv_ou_None, site_utilise_ou_None)."""
    if discover_csv:
        return discover_csv, site
    if site:
        infos = sites.get(site)
        if infos and infos.get("discover_csv"):
            return infos["discover_csv"], site
        print(f"⚠️  Site \"{site}\" introuvable dans {SITES_CONFIG_PATH}.")
        return None, None
    if url_article:
        cle = detecter_site_par_url(url_article, sites)
        if cle:
            infos = sites[cle]
            print(f"→ Site détecté automatiquement : {infos.get('nom', cle)} ({infos.get('domaine', '')})")
            return infos.get("discover_csv"), cle
    env_url = os.environ.get("DISCOVER_CSV_URL")
    if env_url:
        return env_url, None
    if not url_article and sites:
        cle = choisir_site_interactif(sites)
        if cle:
            infos = sites[cle]
            return infos.get("discover_csv"), cle
    return None, None


# ----------------------------------------------------------------------
# 0quater. Contexte URL — catégorie / club / entité déduits de l'URL.
# Ajouté le 4 septembre 2026 sur demande de Fanny : chaque site a sa
# propre structure d'URL (segments de chemin) qui encode souvent déjà le
# club principal, l'événement/la compétition, le type de contenu, ou —
# pour Vringe — le sport de combat concerné. Règles validées par Fanny au
# fil d'une revue site par site des exports Discover 90j (voir la section
# dédiée du document de reprise dashboard-edito pour le détail complet et
# les exemples d'URL par site — cette base est "rolling" : la structure
# peut évoluer si les sites changent leurs URLs, donc sites_categories.json
# est éditable sans toucher au code, sur le même principe que
# sites_discover.json).
#
# Le fichier sites_categories.json décrit, par site, un "type_url" parmi :
#   - "sportsmole"     : /football/{club}/[{événement}/]{type_contenu}/{slug}
#   - "trivela"        : /{pays_ou_thème}/[{compétition}/]{slug} — pas de
#                        club dans l'URL, club/joueur à déduire du slug.
#   - "umdoisesportes" : /{club|topic|"atletiba"|"colunas-e-blogs"}/...
#   - "vringe"         : /[{mma|boks-...}/]{sous-catégorie}/{slug} — sport
#                        de combat (pas forcément football).
#   - "plat"           : une seule URL du type /{slug} (Topmercato,
#                        Afrik-Foot FR) — pas de catégorie dans l'URL.
#   - "prefixe"         : comme "plat" mais précédé d'un préfixe de chemin
#                        fixe (ex: /en-ng/{slug} pour Afrik-Foot Nigeria,
#                        /en-za/{slug} pour Afrik-Foot Afrique du Sud) —
#                        nécessaire car ces variantes partagent le même
#                        nom de domaine qu'Afrik-Foot FR.
#   - "categorie_racine": /[{catégorie}/]{slug} — catégorie optionnelle en
#                        tête, vocabulaire ouvert (ex: MrFixitsTips :
#                        /previews/{slug}, ou directement /{slug}).
#
# Les listes fermées (clubs, topics, types_contenu...) sont volontairement
# NON exhaustives — Fanny, 4 septembre 2026 : "clubs joueurs catégories
# sont non exhaustifs, si nouveau club apparait dans l'url que je teste tu
# peux le prendre en club et c'est donc que ce club n'est pas encore sur
# discover". Un segment à une position "club" qui ne correspond à aucune
# entrée connue est donc traité par défaut comme un NOUVEAU club (signalé
# comme tel dans l'affichage), jamais comme une erreur ou une catégorie
# fourre-tout.
#
# Toutes les valeurs extraites (noms de club, catégories, etc.) restent
# dans leur forme d'origine (pas de traduction) — même règle que pour les
# titres/mots-clés/entités : seuls les LABELS de l'interface sont traduits
# à l'affichage (afficher_contexte_url), jamais les valeurs.
# ----------------------------------------------------------------------

SITES_CATEGORIES_PATH = os.environ.get(
    "SITES_CATEGORIES_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites_categories.json"),
)

# Petite liste de mots-outils multilingue (fr/en/pt-br + translittération
# russe basique) pour filtrer les candidats entités tirés d'un slug d'URL.
# Best-effort volontairement : un slug n'a pas de majuscules, on ne peut
# pas détecter précisément les entités nommées comme dans un titre — voir
# _candidats_slug.
_STOPWORDS_SLUG = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "em", "no", "na", "com",
    "sem", "para", "por", "um", "uma", "se", "que", "mais", "sobre", "entre",
    "apos", "ex", "the", "and", "of", "to", "for", "in", "on", "at", "as",
    "is", "le", "la", "les", "du", "des", "et", "au", "aux", "un", "une",
    "ce", "cette", "son", "sa", "ses", "pour", "dans", "sur", "avec", "vs",
    "v", "apres", "after", "before", "how", "why", "what", "who", "when",
    "where", "will", "from", "with", "this", "that", "its", "not", "are",
    "was", "were", "has", "have", "had", "onde", "como", "qual", "quais",
    "na", "v", "i", "s", "po", "za", "ot", "dlya", "kak", "chto", "eto",
    "vse", "ne", "ili", "ego",
}

# Vocabulaire générique/marketing/clickbait (surtout pronostics & paris
# sportifs — MrFixitsTips, Solobasket, Fotbalportal...) : des mots qui
# reviennent très souvent dans un slug mais ne sont jamais eux-mêmes une
# entité (joueur/club/compétition). Distinct de _STOPWORDS_SLUG (mots-outils
# grammaticaux) — ici ce sont de vrais mots de contenu, juste jamais des
# noms propres. Rolling, non exhaustif — ajout du 4 septembre 2026 sur
# retour de Fanny ("secure ou memorable ne sont pas des entités").
_STOPWORDS_SLUG_GENERIQUE = {
    "secure", "memorable", "send", "off", "bet", "bets", "betting", "builder",
    "tip", "tips", "tipster", "best", "top", "free", "boost", "boosted",
    "bonus", "bonuses", "offer", "offers", "claim", "guide", "review",
    "preview", "previews", "prediction", "predictions", "predicted", "odds",
    "picks", "pick", "acca", "accumulator", "parlay", "correct", "score",
    "scores", "result", "results", "live", "watch", "stream", "streaming",
    "highlights", "latest", "news", "today", "tonight", "week", "day",
    "match", "matches", "game", "games", "fixture", "fixtures", "kickoff",
    "time", "channel", "tv", "verdict", "ratings", "rating", "form",
    "injury", "injuries", "doubt", "doubts", "return", "returns", "value",
    "insight", "analysis", "breakdown", "ahead", "clash", "showdown",
    "battle", "big", "huge", "massive", "major", "ultimate", "key", "stats",
    "explained", "confirmed", "update", "updates", "reaction", "reacts",
    "win", "wins", "winning", "loss", "loses", "beat", "beats", "against",
    "facing", "face", "set", "ready", "edge", "market", "markets", "both",
    "teams", "btts", "over", "under", "goals", "goal", "corners", "cards",
    "card", "clean", "sheet", "sheets", "handicap", "spread", "line",
    "lines", "price", "prices", "bookmaker", "bookies", "signup", "welcome",
    "grab", "here", "now", "final", "finals", "vs", "v",
}


def charger_sites_categories(chemin: str = None) -> dict:
    """Charge le mapping site -> règles de catégorisation d'URL depuis
    sites_categories.json. Renvoie un dict vide (sans planter) si le
    fichier n'existe pas ou est invalide — le script reste utilisable
    sans cette config (juste pas de contexte URL affiché)."""
    chemin = chemin or SITES_CATEGORIES_PATH
    if not os.path.isfile(chemin):
        return {}
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        print(f"⚠️  Impossible de lire {chemin} (JSON invalide ?) — poursuite sans contexte URL.")
        return {}


def _segments_chemin(url: str) -> list:
    """Renvoie les segments du chemin de l'URL (minuscules, sans segment
    vide) — ex: ['football', 'liverpool', 'news', 'xxx_598803.html']."""
    if not url:
        return []
    try:
        chemin = urlparse(url).path
    except ValueError:
        return []
    chemin = (chemin or "").strip("/").lower()
    if not chemin:
        return []
    return [s for s in chemin.split("/") if s]


def _detecter_site_categorie(url: str, sites_categories: dict) -> str:
    """Trouve la clé du site (dans sites_categories.json) correspondant à
    l'URL, en tenant compte du préfixe de chemin pour les variantes
    "prefixe" (ex: afrikfootng/afrikfootza qui partagent le domaine
    afrik-foot.com avec afrikfootfr, distinguées seulement par /en-ng/ ou
    /en-za/ en tête de chemin)."""
    if not url or not sites_categories:
        return None
    domaine_url = _domaine_normalise(url)
    if not domaine_url:
        return None
    segs = _segments_chemin(url)
    candidats_meme_domaine = {
        cle: infos for cle, infos in sites_categories.items()
        if _domaine_normalise((infos or {}).get("domaine", "")) == domaine_url
    }
    if not candidats_meme_domaine:
        return None
    # D'abord les variantes à préfixe (plus spécifiques), puis le "plat".
    for cle, infos in candidats_meme_domaine.items():
        if infos.get("type_url") == "prefixe" and segs and segs[0] == infos.get("prefixe"):
            return cle
    for cle, infos in candidats_meme_domaine.items():
        if infos.get("type_url") not in ("prefixe",):
            # Ne pas confondre avec un chemin qui correspond en fait à un
            # préfixe d'une autre variante du même domaine.
            prefixes_connus = {i.get("prefixe") for i in candidats_meme_domaine.values() if i.get("type_url") == "prefixe"}
            if segs and segs[0] in prefixes_connus:
                continue
            return cle
    return None


def _nettoyer_slug(segment: str) -> str:
    """Retire l'extension de fichier (.html/.htm) d'un segment d'URL."""
    if not segment:
        return ""
    for ext in (".html", ".htm"):
        if segment.endswith(ext):
            return segment[: -len(ext)]
    return segment


def _candidats_slug(segment: str, titre: str = None) -> list:
    """Extrait, en best-effort, des candidats "entité" (club, joueur,
    combattant...) à partir d'un slug d'URL (texte en minuscules, sans
    ponctuation forte de type titre). Filtre les nombres, l'ID final, les
    mots-outils courants (_STOPWORDS_SLUG) et le vocabulaire générique/
    marketing (_STOPWORDS_SLUG_GENERIQUE — ex: "secure", "memorable",
    "send", "off", "bet", jamais des entités même s'ils passent les autres
    filtres).

    Quand `titre` est fourni (vrai titre de l'article, avec majuscules) :
    les entités multi-mots qu'_entites_naive() y détecte (ex: "Kylian
    Mbappé") sont recherchées comme suite CONSÉCUTIVE de tokens dans le
    slug et fusionnées en un seul candidat correctement écrit (accents
    compris), au lieu de rester deux tokens séparés ("Kylian", "Mbappe").
    Un slug seul (minuscules, sans accents) ne permet pas ça de façon
    fiable — d'où l'appui sur le titre réel quand il est disponible.
    Retour de test réel du 4 septembre 2026 (Fanny) : "pas kylian et
    mbappé mais kylian mbappé".

    Sans titre (ex: URLs d'un CSV Discover, sans page chargée), on retombe
    sur le découpage token par token. Résultat volontairement présenté
    comme une liste de "candidats", pas des entités certaines."""
    import re
    slug = _nettoyer_slug(segment)
    if not slug:
        return []
    tokens = [t for t in re.split(r"[-_]+", slug) if t.strip()]

    # Entités multi-mots connues depuis le vrai titre (si fourni), sous
    # forme de listes de tokens normalisés (sans accent) pour matcher les
    # tokens du slug — ex: "Kylian Mbappé" -> ["kylian", "mbappe"].
    entites_titre = []
    if titre:
        for entite in _entites_naive(titre, limite=None):
            toks_entite = [_normaliser(m) for m in entite.split() if m]
            if len(toks_entite) >= 2:
                entites_titre.append((toks_entite, entite))
        # Les entités les plus longues d'abord, pour matcher "kylian mbappe
        # deschamps" (si jamais) avant "kylian mbappe" tout seul.
        entites_titre.sort(key=lambda paire: -len(paire[0]))

    candidats = []
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i].strip()
        if not t:
            i += 1
            continue
        # Essaie d'abord un match multi-mots contre les entités du titre.
        t_norm = _normaliser(t)
        match_trouve = False
        for toks_entite, forme_originale in entites_titre:
            fin = i + len(toks_entite)
            if fin <= n and [_normaliser(tk) for tk in tokens[i:fin]] == toks_entite:
                candidats.append(forme_originale)
                i = fin
                match_trouve = True
                break
        if match_trouve:
            continue
        if t.isdigit() or len(t) < 3:
            i += 1
            continue
        tl = t.lower()
        if tl in _STOPWORDS_SLUG or tl in _STOPWORDS_SLUG_GENERIQUE:
            i += 1
            continue
        candidats.append(_PRETTIFY_OVERRIDES.get(tl, t[:1].upper() + t[1:]))
        i += 1

    vus, resultat = set(), []
    for c in candidats:
        cl = c.lower()
        if cl not in vus:
            vus.add(cl)
            resultat.append(c)
    return resultat[:8]


_PRETTIFY_OVERRIDES = {
    "ufc": "UFC", "mma": "MMA", "dfb-pokal": "DFB-Pokal", "mls": "MLS",
}


def _prettifier_segment(segment: str) -> str:
    """Rend un segment d'URL plus lisible pour l'affichage (tirets ->
    espaces, casse capitalisée), avec quelques exceptions courantes. La
    valeur reste dans sa langue/forme d'origine — seule la mise en forme
    change (pas de traduction)."""
    if not segment:
        return segment
    if segment in _PRETTIFY_OVERRIDES:
        return _PRETTIFY_OVERRIDES[segment]
    return segment.replace("-", " ").replace("_", " ").strip().title()


def _contexte_sportsmole(segs: list, infos: dict) -> dict:
    ctx = {}
    reste = segs[1:] if segs and segs[0] == "football" else list(segs)
    if len(reste) < 2:
        return ctx
    slug = reste[-1]
    avant_slug = reste[:-1]
    types_contenu = set(infos.get("types_contenu", []))
    if len(avant_slug) == 1:
        if avant_slug[0] in types_contenu:
            ctx["type_contenu"] = avant_slug[0]
        else:
            ctx["club_principal"] = _prettifier_segment(avant_slug[0])
    else:
        ctx["club_principal"] = _prettifier_segment(avant_slug[0])
        ctx["type_contenu"] = avant_slug[-1]
        if len(avant_slug) > 2:
            ctx["evenement_competition"] = [_prettifier_segment(s) for s in avant_slug[1:-1]]
    ctx["_slug"] = slug
    return ctx


def _contexte_trivela(segs: list, infos: dict) -> dict:
    ctx = {}
    if len(segs) < 2:
        return ctx
    ctx["categorie"] = _prettifier_segment(segs[0])
    if len(segs) >= 3:
        ctx["evenement_competition"] = [_prettifier_segment(segs[1])]
        ctx["_slug"] = segs[-1]
    else:
        ctx["_slug"] = segs[1]
    return ctx


def _contexte_umdoisesportes(segs: list, infos: dict) -> dict:
    ctx = {}
    if not segs:
        return ctx
    pos0 = segs[0]
    derbys = infos.get("derbys", {})
    clubs = set(infos.get("clubs", []))
    topics = set(infos.get("topics", []))
    segment_chronique = infos.get("segment_chronique", "colunas-e-blogs")
    if pos0 == segment_chronique:
        ctx["categorie"] = "Chronique"
        if len(segs) >= 3:
            ctx["rubrique"] = _prettifier_segment(segs[1])
        if len(segs) == 4:
            ctx["evenement_competition"] = [_prettifier_segment(segs[2])]
            ctx["_slug"] = segs[3]
        elif len(segs) >= 3:
            ctx["_slug"] = segs[2]
    elif pos0 in derbys:
        clubs_derby = derbys[pos0]
        if clubs_derby:
            ctx["club_principal"] = _prettifier_segment(clubs_derby[0])
            if len(clubs_derby) > 1:
                ctx["club_secondaire"] = [_prettifier_segment(c) for c in clubs_derby[1:]]
        ctx["_slug"] = segs[-1]
    elif pos0 in clubs:
        ctx["club_principal"] = _prettifier_segment(pos0)
        ctx["_slug"] = segs[-1]
    elif pos0 in topics:
        ctx["categorie"] = _prettifier_segment(pos0)
        ctx["_slug"] = segs[-1]
    else:
        # Segment inconnu, ni dans "clubs" ni dans "topics" : listes non
        # exhaustives par construction (nouveaux clubs, joueurs, catégories
        # qui apparaissent au fil du temps). Fanny, 4 septembre 2026 :
        # "si nouveau club apparait dans l'url que je teste tu peux le
        # prendre en club et c'est donc que ce club n'est pas encore sur
        # discover" — par défaut, un segment inconnu à cette position est
        # donc traité comme un NOUVEAU club plutôt que comme une catégorie
        # non reconnue (club_nouveau=True signale que ce n'est pas encore
        # confirmé dans la liste connue de sites_categories.json).
        ctx["club_principal"] = _prettifier_segment(pos0)
        ctx["club_nouveau"] = True
        ctx["_slug"] = segs[-1]
    return ctx


def _contexte_vringe(segs: list, infos: dict) -> dict:
    ctx = {}
    if len(segs) < 2:
        return ctx
    slug = segs[-1]
    avant = segs[:-1]
    if len(avant) >= 2:
        marqueur, sous_categorie = avant[0], avant[1]
    else:
        marqueur, sous_categorie = None, avant[0]
    if (marqueur and "mma" in marqueur) or (sous_categorie and "mma" in sous_categorie):
        ctx["type_sport"] = "MMA"
    elif (marqueur and "boks" in marqueur) or (sous_categorie and "boks" in sous_categorie):
        ctx["type_sport"] = "Boxe"
    else:
        ctx["type_sport"] = "Combat (général)"
    ctx["categorie"] = _prettifier_segment(sous_categorie)
    ctx["_slug"] = slug
    return ctx


def _contexte_plat(segs: list, infos: dict) -> dict:
    ctx = {}
    if segs:
        ctx["_slug"] = segs[-1]
    return ctx


def _contexte_prefixe(segs: list, infos: dict) -> dict:
    ctx = {}
    prefixe = infos.get("prefixe")
    reste = segs[1:] if segs and prefixe and segs[0] == prefixe else list(segs)
    if reste:
        ctx["_slug"] = reste[-1]
    return ctx


def _contexte_categorie_racine(segs: list, infos: dict) -> dict:
    """Structure /[{catégorie}/]{slug} — une catégorie optionnelle en tête
    (ex: MrFixitsTips : /previews/{slug}, /horse-racing-tips/{slug}, ou
    directement /{slug} sans catégorie). Catégorie à vocabulaire ouvert,
    non exhaustive — pas de liste fermée à valider."""
    ctx = {}
    if not segs:
        return ctx
    if len(segs) >= 2:
        ctx["categorie"] = _prettifier_segment(segs[0])
    ctx["_slug"] = segs[-1]
    return ctx


_PARSEURS_TYPE_URL = {
    "sportsmole": _contexte_sportsmole,
    "trivela": _contexte_trivela,
    "umdoisesportes": _contexte_umdoisesportes,
    "vringe": _contexte_vringe,
    "plat": _contexte_plat,
    "prefixe": _contexte_prefixe,
    "categorie_racine": _contexte_categorie_racine,
}


def analyser_categorie_url(url: str, sites_categories: dict = None, titre: str = None) -> dict:
    """Point d'entrée : détecte le site (par domaine + préfixe de chemin)
    et applique ses règles pour extraire catégorie / club(s) / type de
    contenu / rubrique / type de sport / candidats entité depuis l'URL.
    Renvoie toujours un dict (vide si rien détecté) — n'échoue jamais,
    cette fonctionnalité est un bonus d'information, jamais bloquante.
    `titre` (optionnel) : le vrai titre de l'article, s'il est déjà connu
    (voir analyser()) — permet à _candidats_slug() de fusionner les
    entités multi-mots ("Kylian Mbappé") au lieu de les laisser en tokens
    séparés. Absent quand on analyse juste une URL de CSV Discover sans
    avoir chargé la page (voir construire_resume_discover)."""
    if sites_categories is None:
        sites_categories = charger_sites_categories()
    resultat = {}
    if not url or not sites_categories:
        return resultat
    site_cle = _detecter_site_categorie(url, sites_categories)
    if not site_cle:
        return resultat
    infos = sites_categories.get(site_cle) or {}
    type_url = infos.get("type_url")
    parseur = _PARSEURS_TYPE_URL.get(type_url)
    if not parseur:
        return resultat
    try:
        ctx = parseur(_segments_chemin(url), infos)
    except Exception:
        return {"site_categorie": site_cle}
    slug = ctx.pop("_slug", None)
    if slug:
        candidats = _candidats_slug(slug, titre=titre)
        if candidats:
            ctx["entites_candidates_slug"] = candidats
    if ctx:
        ctx["site_categorie"] = site_cle
    return ctx


def _normaliser(texte: str) -> str:
    """minuscule + accents retirés, pour un matching robuste."""
    import unicodedata
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode("ascii")
    return texte.lower()


def _entete_normalisee(f: str) -> str:
    """Normalise un en-tête de colonne pour un matching tolérant : accents/
    casse retirés, puis tout caractère non alphanumérique supprimé (gère
    aussi bien "CTR" que "rows.ctr" ou "Taux de clic")."""
    import re
    return re.sub(r"[^a-z0-9]", "", _normaliser(f))


def _detecter_colonnes(fieldnames) -> dict:
    """Associe les en-têtes réels du CSV aux clés normalisées (url/clicks/
    impressions/ctr) en tolérant des variantes de nom/casse/langue, y
    compris les en-têtes bruts d'un export API Search Console non renommé
    (ex: "rows.keys.1", "rows.clicks"). Matching par sous-chaîne plutôt
    qu'égalité stricte, donc "clicks" retrouve aussi bien une colonne
    "Clicks" qu'une colonne "rows.clicks"."""
    import re

    fieldnames = fieldnames or []
    alias_norm = {
        cle: [_entete_normalisee(a) for a in alias]
        for cle, alias in DISCOVER_COL_ALIASES.items()
    }
    candidats = {cle: [] for cle in DISCOVER_COL_ALIASES}
    for f in fieldnames:
        f_norm = _entete_normalisee(f)
        for cle, alias_list in alias_norm.items():
            if any(a and a in f_norm for a in alias_list):
                candidats[cle].append(f)

    def _indice_final(f):
        """Pour départager plusieurs colonnes candidates (ex: rows.keys.0
        ET rows.keys.1 dans un export multi-dimensions) : on privilégie
        l'indice le plus élevé, qui correspond en général à la dernière
        dimension demandée à l'API (la page/l'URL vient après la date)."""
        chiffres = re.findall(r"\d+", f)
        return int(chiffres[-1]) if chiffres else -1

    mapping = {}
    for cle, cands in candidats.items():
        if cands:
            mapping[cle] = max(cands, key=_indice_final)
    return mapping


def charger_discover_csv(csv_url: str) -> list:
    """Télécharge et parse le CSV Discover publié depuis Google Sheets
    (Fichier > Partager > Publier sur le web > format CSV). Renvoie une
    liste de dicts {url, clicks, impressions, ctr}. Lève une exception
    explicite si le téléchargement ou le format échoue."""
    import csv
    import io

    resp = requests.get(csv_url, timeout=20)
    resp.raise_for_status()
    texte = resp.content.decode("utf-8-sig", errors="replace")  # gère le BOM Excel/Sheets

    reader = csv.DictReader(io.StringIO(texte))
    mapping = _detecter_colonnes(reader.fieldnames)
    manquantes = [c for c in ("url", "clicks", "impressions") if c not in mapping]
    if manquantes:
        raise ValueError(
            f"Colonnes introuvables dans le CSV Discover : {manquantes}. "
            f"En-têtes détectés : {reader.fieldnames}. "
            f"Renomme les colonnes du Google Sheet (URL / Clicks / Impressions / CTR) "
            f"ou ajoute leur variante à DISCOVER_COL_ALIASES dans le script."
        )

    lignes = []
    for row in reader:
        try:
            clicks_raw = row.get(mapping.get("clicks"), "0") or "0"
            clicks = int(float(str(clicks_raw).replace(",", ".").replace(" ", "") or 0))
        except Exception:
            clicks = 0
        try:
            impressions_raw = row.get(mapping["impressions"], "0") or "0"
            impressions = int(float(str(impressions_raw).replace(",", ".").replace(" ", "") or 0))
        except Exception:
            impressions = 0
        ctr = None
        if "ctr" in mapping:
            try:
                ctr_raw = str(row.get(mapping["ctr"], "")).replace("%", "").replace(",", ".").strip()
                ctr = float(ctr_raw) if ctr_raw else None
            except Exception:
                ctr = None
        lignes.append({
            "url": (row.get(mapping["url"]) or "").strip(),
            "clicks": clicks,
            "impressions": impressions,
            "ctr": ctr,
        })
    return lignes


def _tokeniser_entite(entite: str) -> list:
    """Découpe le nom d'une entité (joueur/club/entraîneur/compétition) en
    mots normalisés, TOUS requis pour qu'une URL matche — contrairement à
    l'ancien matching "n'importe quel mot du mot-clé/cluster" qui remontait
    trop de faux positifs sur des mots isolés trop communs. Pas de filtre
    de longueur agressif : des sigles courts comme "OL"/"OM"/"PSG" doivent
    rester utilisables."""
    return [
        mot for mot in _normaliser(entite or "").replace("-", " ").replace("'", " ").split()
        if len(mot) >= 2
    ]


def _matcher_entite(tokens: list, lignes_discover: list) -> list:
    if not tokens:
        return []
    matches = []
    for l in lignes_discover:
        if not l["url"]:
            continue
        slug = _normaliser(l["url"])
        if all(tok in slug for tok in tokens):
            matches.append(l)
    matches.sort(key=lambda l: l["clicks"] + l["impressions"], reverse=True)
    return matches


def _radical(mot: str, taille: int = 5) -> str:
    """Racine approximative d'un mot (préfixe normalisé) — tolère les
    variantes grammaticales courantes (pluriel, accord, conjugaison légère)
    sans dépendance à une bibliothèque de stemming : "officialise" et
    "officialisée" partagent la racine "offic"."""
    m = _normaliser(mot)
    return m[:taille] if len(m) >= taille else m


def entite_dans_textes(entite: str, textes: list) -> bool:
    """Vrai si TOUS les mots de l'entité apparaissent (tels quels ou en
    variante proche du même radical) dans AU MOINS UN des textes fournis
    (utilisé pour vérifier si un mot-clé/une entité figure dans les H2 —
    section 15.2/15.10bis). Ex: entité "Djaoui Cissé" matche un H2 "Djaoui
    Cissé signe à Sunderland" mais pas un H2 qui ne cite que "Rennes"."""
    tokens = [t for t in _tokeniser_entite(entite) if len(t) >= 3]
    if not tokens:
        return False
    radicaux = [_radical(t) for t in tokens]
    textes_normalises = [_normaliser(t) for t in textes]
    return any(all(r in tn for r in radicaux) for tn in textes_normalises)


def reality_check(url_article: str, entites: list, lignes_discover: list, jours: int = DISCOVER_JOURS_DEFAUT) -> dict:
    """Compare l'article aux vraies données Discover, entité par entité
    (voir IA_TOOL_SCHEMA/entites_principales — jusqu'à 3 noms propres
    précis : joueur, club, entraîneur, compétition). Pour chaque entité,
    matche les URLs Discover qui contiennent TOUS ses mots, et renvoie des
    MOYENNES par article trouvé (pas des cumuls) — plus lisible pour juger
    du potentiel d'un sujet indépendamment du nombre d'articles déjà publiés
    dessus. Le CTR moyen reste calculé en poolant clics/impressions (plus
    fiable qu'une moyenne simple de CTR quand les volumes diffèrent
    beaucoup d'un article à l'autre)."""
    url_norm = _normaliser(url_article)
    deja_sur_discover = any(_normaliser(l["url"]) == url_norm for l in lignes_discover if l["url"])

    resultats = []
    for entite in (entites or [])[:3]:
        tokens = _tokeniser_entite(entite)
        matches = _matcher_entite(tokens, lignes_discover)
        n = len(matches)
        clics_totaux = sum(l["clicks"] for l in matches)
        impressions_totales = sum(l["impressions"] for l in matches)
        resultats.append({
            "entite": entite,
            "nb_articles_topic": n,
            "clics_moyens_par_article": round(clics_totaux / n) if n else None,
            "impressions_moyennes_par_article": round(impressions_totales / n) if n else None,
            "ctr_moyen_pct": round(100 * clics_totaux / impressions_totales, 2) if impressions_totales else None,
            "top_exemples": [
                {
                    "url": l["url"],
                    "clicks": l["clicks"],
                    "impressions": l["impressions"],
                    # CTR de la ligne elle-même (celui du CSV si présent,
                    # sinon calculé) — distinct du ctr_moyen_pct de l'entité
                    # ci-dessus, qui lui est poolé sur tous les articles.
                    "ctr": l["ctr"] if l.get("ctr") is not None else (
                        round(100 * l["clicks"] / l["impressions"], 2) if l["impressions"] else None
                    ),
                }
                for l in matches[:5]
            ],
        })

    return {
        "periode_jours": jours,
        "entites_recherchees": list(entites or [])[:3],
        "deja_sur_discover_cette_url": deja_sur_discover,
        "resultats": resultats,
    }


def construire_resume_discover(lignes_discover: list, jours: int = DISCOVER_JOURS_DEFAUT, sites_categories: dict = None) -> dict:
    """Résumé générique Discover (5 lignes), demandé par Fanny le 4
    septembre 2026, affiché aussi bien en mode URL qu'en mode --titre :
    "Les 90 derniers jours, il y a eu X URLs sur Discover avec impressions
    moyennes de X, clicks moyens de X, CTR moyen de X et voici les sujets
    sélectionnés : XXX". Les "sujets sélectionnés" sont les clubs/catégories
    les plus fréquents parmi les URLs du CSV (déduits via
    analyser_categorie_url — indépendant du site pour lequel le CSV a été
    choisi, la détection se fait par domaine sur chaque URL individuelle).
    Ne plante jamais : renvoie {} si aucune ligne."""
    if not lignes_discover:
        return {}
    n = len(lignes_discover)
    total_clics = sum(l.get("clicks", 0) for l in lignes_discover)
    total_impressions = sum(l.get("impressions", 0) for l in lignes_discover)

    if sites_categories is None:
        sites_categories = charger_sites_categories()
    compteur = {}
    for l in lignes_discover:
        ctx = analyser_categorie_url(l.get("url", ""), sites_categories)
        sujet = ctx.get("club_principal") or ctx.get("categorie")
        if sujet:
            compteur[sujet] = compteur.get(sujet, 0) + 1
    sujets_tries = sorted(compteur.items(), key=lambda kv: (-kv[1], kv[0]))

    return {
        "periode_jours": jours,
        "nb_urls": n,
        "impressions_moyennes": round(total_impressions / n, 1),
        "clics_moyens": round(total_clics / n, 1),
        "ctr_moyen_pct": round(100 * total_clics / total_impressions, 2) if total_impressions else None,
        "sujets_principaux": [s for s, _ in sujets_tries[:8]],
    }


# ----------------------------------------------------------------------
# 5. Analyse complète
# ----------------------------------------------------------------------

def analyser(url: str, html: str, avec_ia: bool = False, ia_mock: bool = False, ia_openai: bool = False, avec_reality_check: bool = False, discover_csv_url: str = None, discover_jours: int = DISCOVER_JOURS_DEFAUT, langue: str = LANGUE_DEFAUT) -> RapportQualite:
    soup = BeautifulSoup(html, "html.parser")

    titre_tag = soup.find("h1") or soup.find("title")
    titre = titre_tag.get_text(strip=True) if titre_tag else ""

    body = extract_article_body(soup)

    # Embeds sociaux (tweets, Instagram, Facebook) : comptés à part, puis
    # retirés du corps AVANT le comptage mots/liens — leurs liens internes
    # (hashtags, mentions, t.co...) ne sont pas "du texte" écrit par l'auteur.
    embeds = find_social_embeds(body)
    remove_social_embeds(body, embeds)

    # Périmètre "texte courant" : seulement les <p> qui ne sont pas dans un
    # widget (articles similaires, partage, tags, commentaires...). Si aucun
    # <p> n'est trouvé (rare), on retombe sur tout le corps identifié.
    core_paragraphs = extract_core_paragraphs(body)
    if core_paragraphs:
        body_text = " ".join(p.get_text(" ", strip=True) for p in core_paragraphs)
        liens = [a for p in core_paragraphs for a in p.find_all("a") if a.get("href")]
    else:
        body_text = body.get_text(separator=" ", strip=True)
        liens = [a for a in body.find_all("a") if a.get("href")]
    core_h2 = extract_core_h2(body)

    rapport = RapportQualite(url=url, titre=titre)
    rapport.contexte_url = analyser_categorie_url(url, titre=titre)

    # Éligibilité technique Discover (section 4ter) — sur le HTML complet
    # (pas le corps filtré), car les meta/JSON-LD sont dans <head>.
    rapport.criteres_discover = verifier_eligibilite_discover(soup, url, langue)

    rapport.criteres = [
        check_https(url, langue),
        critere_manuel("titre_coherent", "label_titre_coherent", langue),
        critere_manuel("sourcing_present", "label_sourcing_present", langue),
        check_liens_qualifies(liens, langue),
        check_image_credit(body, langue),
        check_word_count(body_text, langue),
        check_hyperlinks(liens, langue),
        check_embeds_sociaux(embeds, langue),
        check_h2_count(core_h2, langue),
        critere_manuel(
            "h2_mot_cle", "label_h2_mot_cle", langue,
            detail_cle="detail_h2_mot_cle_defaut",
        ),
    ]

    # Texte à donner à une IA (ou un humain) pour juger les critères "manuel"
    rapport.texte_pour_review_ia = body_text[:4000]
    rapport.liens_detectes = [
        {"href": a.get("href"), "texte": a.get_text(strip=True)[:80]} for a in liens
    ]

    if ia_mock:
        rapport.analyse_ia = analyser_ia_mock(titre, url, body_text, langue)
    elif ia_openai:
        rapport.analyse_ia = analyser_ia_openai(titre, url, body_text, langue)
    elif avec_ia:
        rapport.analyse_ia = analyser_ia(titre, url, body_text, langue)

    # Fusionne le(s) club(s) déduits de l'URL (contexte_url, section
    # 0quater) dans les entités identifiées par l'IA/mock : un club lu
    # explicitement dans l'URL est plus fiable qu'une entité devinée dans
    # le texte, donc placé en tête (dédoublonnage insensible à la casse).
    # Alimente ensuite aussi bien le critère h2_mot_cle que le reality
    # check ci-dessous. Demande de Fanny du 4 septembre 2026.
    if rapport.analyse_ia and rapport.contexte_url:
        clubs_url = []
        if rapport.contexte_url.get("club_principal"):
            clubs_url.append(rapport.contexte_url["club_principal"])
        clubs_url.extend(rapport.contexte_url.get("club_secondaire") or [])
        if clubs_url:
            entites_actuelles = rapport.analyse_ia.get("entites_principales") or []
            vus = {_normaliser(e) for e in clubs_url}
            fusion = list(clubs_url)
            for e in entites_actuelles:
                if _normaliser(e) not in vus:
                    vus.add(_normaliser(e))
                    fusion.append(e)
            rapport.analyse_ia["entites_principales"] = fusion[:3]

    # Reporte le jugement IA sur les 2 critères "manuel" restants de la
    # grille 15.2 (titre_coherent, sourcing_present — sponsoring_declare et
    # pas_doublon_interne ont été retirés de la grille le 4 septembre 2026).
    # Le commentaire lui-même vient déjà de l'IA/mock dans la bonne langue
    # (voir construire_system_prompt_ia / analyser_ia_mock) — on ne traduit
    # ici que le suffixe fixe ([SIMULÉ], "jugé par IA...").
    lecture = rapport.analyse_ia.get("lecture_editoriale") if rapport.analyse_ia else None
    if lecture:
        suffixe = _t("suffixe_simule", langue) if rapport.analyse_ia.get("_simule") else ""
        suffixe += _t("suffixe_juge_par_ia", langue)
        for c in rapport.criteres:
            bloc = lecture.get(c.cle)
            if not bloc:
                continue
            c.statut = bloc.get("statut", "a_verifier")
            c.detail = f"{bloc.get('commentaire', '')}{suffixe}"

    # Critère "mot-clé principal présent dans un H2" — dérivé localement
    # (pas de champ IA dédié) : on réutilise les entités déjà identifiées
    # (entites_principales) et les H2 déjà extraits (core_h2), et on
    # vérifie la présence de l'une d'elles, telle quelle ou en variante
    # proche du même radical (voir entite_dans_textes). Ajouté le 4
    # septembre 2026 sur demande de Fanny. Les NOMS d'entités interpolés
    # dans le message ne sont jamais traduits (extraits littéraux du
    # texte/titre de l'article).
    crit_h2_mot_cle = next((c for c in rapport.criteres if c.cle == "h2_mot_cle"), None)
    if crit_h2_mot_cle is not None and rapport.analyse_ia:
        search_block = rapport.analyse_ia.get("search") or {}
        entites_ia = rapport.analyse_ia.get("entites_principales") or []
        h2_textes = [h.get_text(" ", strip=True) for h in core_h2]
        if not search_block.get("positionnable_seo"):
            crit_h2_mot_cle.statut = "ok"
            crit_h2_mot_cle.detail = _t("h2_mot_cle_non_applicable", langue)
        elif not h2_textes:
            crit_h2_mot_cle.statut = "warning"
            crit_h2_mot_cle.detail = _t("h2_mot_cle_aucun_h2", langue)
        else:
            trouve = next((e for e in entites_ia if entite_dans_textes(e, h2_textes)), None)
            if trouve:
                crit_h2_mot_cle.statut = "ok"
                crit_h2_mot_cle.detail = _t("h2_mot_cle_trouve", langue, entite=trouve)
            else:
                liste = ", ".join(entites_ia) if entites_ia else _t("aucune_entite_identifiee", langue)
                crit_h2_mot_cle.statut = "warning"
                crit_h2_mot_cle.detail = _t("h2_mot_cle_non_trouve", langue, liste=liste)

    if avec_reality_check:
        entites = rapport.analyse_ia.get("entites_principales") if rapport.analyse_ia else None
        if not entites:
            rapport.reality_check = {"erreur": _t("err_rc_besoin_ia", langue)}
        elif not discover_csv_url:
            rapport.reality_check = {"erreur": _t("err_csv_manquant", langue)}
        else:
            try:
                lignes_discover = charger_discover_csv(discover_csv_url)
                rapport.reality_check = reality_check(url, entites, lignes_discover, jours=discover_jours)
                rapport.discover_resume = construire_resume_discover(lignes_discover, discover_jours)
            except Exception as e:
                rapport.reality_check = {"erreur": _t("err_csv_chargement", langue, e=e)}

    return rapport


# ----------------------------------------------------------------------
# 5bis. Mode "idée d'article" (section 15.11) — pas d'URL ni de texte,
#       juste un titre envisagé, pour arbitrer si un sujet vaut le coup
#       d'être écrit AVANT de l'écrire : identifie le mot-clé/les entités,
#       puis simule le potentiel Discover réel sur ces entités (mêmes
#       fonctions que le reality check standard, appliquées à un titre nu).
# ----------------------------------------------------------------------

def analyser_idee(titre: str, avec_ia: bool = False, ia_mock: bool = False, ia_openai: bool = False, discover_csv_url: str = None, discover_jours: int = DISCOVER_JOURS_DEFAUT, langue: str = LANGUE_DEFAUT) -> dict:
    if ia_mock:
        analyse = analyser_ia_mock_titre(titre, langue)
    elif ia_openai:
        analyse = analyser_ia_openai(titre, "", titre, langue)
    elif avec_ia:
        # Réutilise le vrai appel API (même schéma qu'un article complet),
        # avec le titre comme seul "texte" — search/entités restent
        # exploitables depuis un titre seul ; discover/lecture_editoriale
        # le sont moins (pas de vrai corps à lire), donc pas affichés ici.
        analyse = analyser_ia(titre, "", titre, langue)
    else:
        return {
            "titre": titre,
            "analyse_ia": {"erreur": _t("err_titre_besoin_ia", langue)},
            "reality_check": None,
        }

    resultat = {"titre": titre, "analyse_ia": analyse, "reality_check": None}
    if "erreur" in analyse:
        return resultat

    entites = analyse.get("entites_principales") or []
    resultat["discover_resume"] = None
    if not discover_csv_url:
        resultat["reality_check"] = {"erreur": _t("err_csv_manquant", langue)}
    else:
        try:
            lignes_discover = charger_discover_csv(discover_csv_url)
            # Résumé générique (X URLs, moyennes, sujets) : indépendant des
            # entités identifiées pour ce titre précis, donc calculé même
            # si aucune entité n'a pu être identifiée.
            resultat["discover_resume"] = construire_resume_discover(lignes_discover, discover_jours)
            if not entites:
                resultat["reality_check"] = {"erreur": _t("err_aucune_entite_titre", langue)}
            else:
                # Pas d'URL réelle (l'article n'existe pas encore) : "" ne
                # matchera jamais aucune ligne Discover, deja_sur_discover
                # reste donc toujours à False ici, ce qui est le comportement
                # attendu pour une idée pas encore publiée.
                resultat["reality_check"] = reality_check("", entites, lignes_discover, jours=discover_jours)
        except Exception as e:
            resultat["reality_check"] = {"erreur": _t("err_csv_chargement", langue, e=e)}

    return resultat


# ----------------------------------------------------------------------
# 6. Affichage
# ----------------------------------------------------------------------

ICONES = {"ok": "✅", "warning": "⚠️", "fail": "❌", "a_verifier": "🔎"}


def afficher(rapport: RapportQualite, langue: str = LANGUE_DEFAUT):
    print(f"\n{_t('titre_rapport', langue)}")
    print(f"{_t('label_url', langue)}   : {rapport.url}")
    print(f"{_t('label_titre', langue)} : {rapport.titre}\n")
    for c in rapport.criteres:
        icone = ICONES.get(c.statut, "•")
        print(f"{icone}  {c.label:38s} {c.detail}")

    crit_h2 = next((c for c in rapport.criteres if c.cle == "nombre_h2"), None)
    if crit_h2 is not None and isinstance(crit_h2.valeur, dict) and crit_h2.valeur.get("textes"):
        print(f"\n{_t('h2_detectes', langue)}")
        for i, texte in enumerate(crit_h2.valeur["textes"], 1):
            print(f"  {i}. {texte}")

    restants = [c for c in rapport.criteres if c.statut == "a_verifier"]
    if restants:
        print(f"\n{_t('recap_a_verifier', langue)}")
        for c in restants:
            print(f"  🔎 {c.label}")
        print()

    if rapport.criteres_discover:
        afficher_discover_eligibilite(rapport.criteres_discover, langue)

    if rapport.contexte_url:
        afficher_contexte_url(rapport.contexte_url, langue)

    if rapport.analyse_ia:
        afficher_ia(rapport.analyse_ia, langue)

    if rapport.discover_resume:
        afficher_discover_resume(rapport.discover_resume, langue)

    if rapport.reality_check:
        afficher_reality_check(rapport.reality_check, langue)


def afficher_discover_eligibilite(criteres: list, langue: str = LANGUE_DEFAUT):
    """Affiche la section d'éligibilité technique Discover (section 4ter)."""
    if not criteres:
        return
    print(f"\n{_t('de_titre_bloc', langue)}")
    nb_fail = sum(1 for c in criteres if c.statut == "fail")
    nb_warn = sum(1 for c in criteres if c.statut == "warning")
    nb_ok = sum(1 for c in criteres if c.statut == "ok")
    for c in criteres:
        icone = ICONES.get(c.statut, "•")
        print(f"{icone}  {c.label:38s} {c.detail}")
    # Synthèse rapide
    if nb_fail > 0:
        print(f"\n  ⛔ {nb_fail} bloquant(s) détecté(s) — l'article NE PEUT PAS apparaître dans Discover en l'état.")
    elif nb_warn > 0:
        print(f"\n  ⚠️  {nb_ok}/{len(criteres)} OK, {nb_warn} avertissement(s) — éligible mais pas optimal.")
    else:
        print(f"\n  ✅ {nb_ok}/{len(criteres)} critères techniques validés — éligible Discover.")
    print()


def afficher_contexte_url(ctx: dict, langue: str = LANGUE_DEFAUT):
    """Affiche le club/la catégorie/le type de contenu déduits de l'URL
    (section 0quater) — n'affiche que les champs effectivement présents
    (chaque site n'en fournit qu'une partie selon sa structure d'URL). Les
    VALEURS (noms de club, catégories...) ne sont jamais traduites, comme
    les titres/mots-clés/entités — seuls les labels le sont."""
    if not any(k != "site_categorie" for k in ctx):
        return
    print(_t("ctx_titre_bloc", langue))
    if ctx.get("club_principal"):
        print(f"{_t('ctx_club_principal', langue)}{ctx['club_principal']}")
        if ctx.get("club_nouveau"):
            print(_t("ctx_club_nouveau_note", langue))
    if ctx.get("club_secondaire"):
        print(f"{_t('ctx_club_secondaire', langue)}{', '.join(ctx['club_secondaire'])}")
    if ctx.get("categorie"):
        print(f"{_t('ctx_categorie', langue)}{ctx['categorie']}")
    if ctx.get("type_contenu"):
        print(f"{_t('ctx_type_contenu', langue)}{ctx['type_contenu']}")
    if ctx.get("evenement_competition"):
        print(f"{_t('ctx_evenement_competition', langue)}{', '.join(ctx['evenement_competition'])}")
    if ctx.get("rubrique"):
        print(f"{_t('ctx_rubrique', langue)}{ctx['rubrique']}")
    if ctx.get("type_sport"):
        print(f"{_t('ctx_type_sport', langue)}{ctx['type_sport']}")
    if ctx.get("entites_candidates_slug"):
        print(f"{_t('ctx_entites_candidates', langue)}{', '.join(ctx['entites_candidates_slug'])}")
    print()


def afficher_discover_resume(dr: dict, langue: str = LANGUE_DEFAUT):
    """Résumé générique Discover (5 lignes, section 0quater/16), affiché
    aussi bien en mode URL qu'en mode --titre. Les "sujets sélectionnés"
    (noms de club/catégorie) ne sont pas traduits."""
    if not dr:
        return
    jours = dr.get("periode_jours", DISCOVER_JOURS_DEFAUT)
    print(_t("dr_titre_bloc", langue, jours=jours))
    print(_t("dr_nb_urls", langue, n=dr.get("nb_urls", 0)))
    print(_t("dr_impressions", langue, v=dr.get("impressions_moyennes")))
    ctr = dr.get("ctr_moyen_pct")
    ctr_txt = f"{ctr}%" if ctr is not None else _t("na", langue)
    print(_t("dr_clics_ctr", langue, clics=dr.get("clics_moyens"), ctr=ctr_txt))
    sujets = ", ".join(dr.get("sujets_principaux") or []) or _t("aucune", langue)
    print(_t("dr_sujets", langue, sujets=sujets))
    print()


def afficher_reality_check(rc: dict, langue: str = LANGUE_DEFAUT):
    jours = rc.get("periode_jours", DISCOVER_JOURS_DEFAUT)
    print(_t("rc_titre_bloc", langue, jours=jours))
    if "erreur" in rc:
        print(f"  ⚠️  {rc['erreur']}\n")
        return

    entites_txt = ", ".join(rc["entites_recherchees"]) or _t("aucune", langue)
    print(f"{_t('rc_entites_recherchees', langue)}{entites_txt}")
    if rc["deja_sur_discover_cette_url"]:
        print(f"  {_t('rc_url_deja_presente', langue)}")
    else:
        print(f"  {_t('rc_url_absente', langue)}")
    print()

    for res in rc["resultats"]:
        n = res["nb_articles_topic"]
        if n == 0:
            print(_t("rc_entite_zero", langue, entite=res["entite"], jours=jours))
            print(_t("rc_entite_zero_note", langue))
        else:
            print(_t("rc_entite_trouvee", langue, entite=res["entite"], n=n, jours=jours))
            print(_t("rc_clics_moyens", langue, v=res["clics_moyens_par_article"]))
            print(_t("rc_impressions_moyennes", langue, v=res["impressions_moyennes_par_article"]))
            if res["ctr_moyen_pct"] is not None:
                print(_t("rc_ctr_moyen", langue, v=res["ctr_moyen_pct"]))
            print(_t("rc_exemples", langue))
            for ex in res["top_exemples"]:
                ctr_txt = f"{ex['ctr']}%" if ex["ctr"] is not None else _t("na", langue)
                print(_t("rc_exemple_ligne", langue, clicks=ex["clicks"], impressions=ex["impressions"], ctr=ctr_txt, url=ex["url"]))
        print()


def afficher_ia(ia: dict, langue: str = LANGUE_DEFAUT):
    suffixe = _t("ia_suffixe_simule", langue) if ia.get("_simule") else ""
    print(_t("ia_titre_bloc", langue, suffixe=suffixe))
    if "erreur" in ia:
        print(f"  ⚠️  {ia['erreur']}\n")
        return

    s = ia["search"]
    print(_t("ia_search_label", langue))
    if s.get("positionnable_seo"):
        print(f"{_t('ia_mot_cle_principal', langue)}{s.get('mot_cle_principal')}")
        print(f"{_t('ia_cluster_thematique', langue)}{s.get('cluster_thematique')}")
        score = s.get("score_lisibilite_mot_cle")
        print(_t("ia_score_lisibilite", langue, score=score) if score is not None else _t("ia_score_na", langue))
    else:
        print(_t("ia_pas_de_mot_cle", langue))
        print(_t("ia_pas_de_mot_cle_note", langue))
    print(f"  → {s.get('justification', '')}")

    print(f"\n{_t('ia_discover_label', langue)}")
    d = ia["discover"]
    score = d.get("score", 0)
    icone = "✅" if score >= 80 else "⚠️" if score >= 50 else "❌"
    print(_t("ia_score_discover", langue, icone=icone, score=score))
    if d.get("points_forts"):
        print(_t("ia_points_forts", langue) + "; ".join(d["points_forts"]))
    if d.get("points_faibles"):
        print(_t("ia_points_faibles", langue) + "; ".join(d["points_faibles"]))
    print(f"  → {d.get('justification', '')}")
    print()


def afficher_idee(resultat: dict, langue: str = LANGUE_DEFAUT):
    """Affichage du mode --titre (section 15.11) : mot-clé/entités identifiés
    depuis le seul titre envisagé, puis potentiel Discover réel sur ces
    entités — pas de grille de critères qualité ici, l'article n'existe
    pas encore."""
    print(f"\n{_t('idee_titre_bloc', langue)}")
    print(f"{_t('idee_titre_envisage', langue, titre=resultat['titre'])}\n")

    analyse = resultat.get("analyse_ia") or {}
    if "erreur" in analyse:
        print(f"⚠️  {analyse['erreur']}\n")
        return

    suffixe = _t("suffixe_simule", langue) if analyse.get("_simule") else ""
    print(f"--- {_t('header_search_seo', langue)}{suffixe} ---")
    s = analyse.get("search", {})
    if s.get("positionnable_seo"):
        print(f"{_t('ia_mot_cle_principal', langue)}{s.get('mot_cle_principal')}")
        print(f"{_t('ia_cluster_thematique', langue)}{s.get('cluster_thematique')}")
        score = s.get("score_lisibilite_mot_cle")
        print(_t("ia_score_lisibilite", langue, score=score) if score is not None else _t("ia_score_na", langue))
    else:
        print(_t("idee_pas_de_mot_cle", langue))
    if s.get("justification"):
        print(f"  → {s['justification']}")
    entites = analyse.get("entites_principales") or []
    entites_txt = ", ".join(entites) or _t("aucune", langue)
    print(f"{_t('idee_entites_identifiees', langue)}{entites_txt}")
    print()

    dr = resultat.get("discover_resume")
    if dr:
        afficher_discover_resume(dr, langue)

    rc = resultat.get("reality_check")
    if rc:
        afficher_reality_check(rc, langue)


# ----------------------------------------------------------------------
# 7. Données fake (étape 1 du plan — valider la mécanique avant une vraie URL)
# ----------------------------------------------------------------------

FAKE_ARTICLES = {
    "good": {
        "url": "https://exemple-foot.fake/psg-mercato-officiel",
        "html": """
        <html><head><title>PSG : le club officialise l'arrivée d'un nouveau défenseur</title></head>
        <body>
        <article>
            <h1>PSG : le club officialise l'arrivée d'un nouveau défenseur</h1>
            <figure>
                <img src="joueur.jpg" alt="Le nouveau défenseur du PSG lors de sa présentation officielle">
                <figcaption>Photo : PSG Communication</figcaption>
            </figure>
            <p>Le Paris Saint-Germain a officialisé ce mercredi l'arrivée d'un nouveau défenseur central,
            comme l'a confirmé le club dans un <a href="https://psg.fr/communique" rel="nofollow">communiqué officiel</a>.
            Le joueur, âgé de 24 ans, s'est engagé pour quatre saisons.</p>
            <p>Selon les informations de <a href="https://lequipe.fr/transfert-psg" rel="nofollow">L'Équipe</a>,
            le montant du transfert avoisine les 40 millions d'euros, bonus compris. Le joueur a passé
            sa visite médicale la semaine dernière avant de signer son contrat.</p>
            <p>Il portera le numéro 4 et pourrait faire ses débuts dès le prochain match de championnat.
            L'entraîneur a salué "un renfort important pour la défense" lors de sa conférence de presse.
            Ce transfert s'inscrit dans la stratégie de recrutement du club pour la saison à venir, qui vise
            à renforcer un secteur défensif jugé perfectible la saison passée.</p>
            <p>Plus de détails seront communiqués lors de la présentation officielle du joueur, prévue
            la semaine prochaine au centre d'entraînement du club, en présence de la presse accréditée.</p>
            <p>
            <blockquote class="twitter-tweet">
                <p lang="fr" dir="ltr">Officiel : le PSG annonce l'arrivée de son nouveau défenseur ! <a href="https://x.com/hashtag/PSG?src=hash">#PSG</a></p>
                &mdash; Ligue 1 Actu (<a href="https://x.com/Ligue1Actu">@Ligue1Actu</a>)
                <a href="https://x.com/Ligue1Actu/status/123456789">3 septembre 2026</a>
                <a href="https://t.co/abc123XYZ">pic.twitter.com/abc123XYZ</a>
            </blockquote>
            </p>
        </article>
        </body></html>
        """,
    },
    "bad": {
        "url": "https://exemple-clickbait.fake/vous-ne-devinerez-jamais",
        "html": """
        <html><head><title>Vous ne devinerez JAMAIS ce qui est arrivé à cette star du foot !</title></head>
        <body>
        <div class="content">
            <h1>Vous ne devinerez JAMAIS ce qui est arrivé à cette star du foot !</h1>
            <p>Incroyable nouvelle dans le monde du foot aujourd'hui.</p>
            <p>Cliquez pour en savoir plus sur <a href="https://boutique-partenaire.fake/promo">notre offre exclusive</a>.</p>
        </div>
        </body></html>
        """,
    },
}


def run_fake(nom: str, avec_ia: bool = False, ia_mock: bool = False, ia_openai: bool = False, avec_reality_check: bool = False, discover_csv_url: str = None, discover_jours: int = DISCOVER_JOURS_DEFAUT, langue: str = LANGUE_DEFAUT):
    if nom not in FAKE_ARTICLES:
        print(f"Jeu de données fake inconnu : {nom}. Choix possibles : {list(FAKE_ARTICLES)}")
        sys.exit(1)
    data = FAKE_ARTICLES[nom]
    rapport = analyser(data["url"], data["html"], avec_ia=avec_ia, ia_mock=ia_mock, ia_openai=ia_openai, avec_reality_check=avec_reality_check, discover_csv_url=discover_csv_url, discover_jours=discover_jours, langue=langue)
    afficher(rapport, langue)
    return rapport


def run_url(url: str, avec_ia: bool = False, ia_mock: bool = False, ia_openai: bool = False, avec_reality_check: bool = False, discover_csv_url: str = None, discover_jours: int = DISCOVER_JOURS_DEFAUT, langue: str = LANGUE_DEFAUT):
    html = fetch_html(url)
    rapport = analyser(url, html, avec_ia=avec_ia, ia_mock=ia_mock, ia_openai=ia_openai, avec_reality_check=avec_reality_check, discover_csv_url=discover_csv_url, discover_jours=discover_jours, langue=langue)
    afficher(rapport, langue)
    return rapport


def run_idee(titre: str, avec_ia: bool = False, ia_mock: bool = False, ia_openai: bool = False, discover_csv_url: str = None, discover_jours: int = DISCOVER_JOURS_DEFAUT, langue: str = LANGUE_DEFAUT):
    resultat = analyser_idee(titre, avec_ia=avec_ia, ia_mock=ia_mock, ia_openai=ia_openai, discover_csv_url=discover_csv_url, discover_jours=discover_jours, langue=langue)
    afficher_idee(resultat, langue)
    return resultat


def run_test_tous_sites(sites: dict, avec_ia: bool = False, ia_mock: bool = False, ia_openai: bool = False, discover_jours: int = DISCOVER_JOURS_DEFAUT, langue: str = LANGUE_DEFAUT):
    """Mode --test-tous-sites (section 15.15), ajouté le 4 septembre 2026
    sur demande de Fanny : "possible d'avoir code pour tester plusieurs
    urls d'un coup ? prends une url par site au hasard (par exemple du
    fichier discover), juste pour tester le fonctionnement". Pour chaque
    site configuré dans sites_discover.json : pioche une URL au hasard
    dans son CSV Discover, récupère la vraie page, et lance le rapport
    complet dessus (avec --reality-check sur le CSV du même site) — un
    smoke test rapide sur tous les sites d'un coup, sans avoir à copier
    13 URLs une par une. N'échoue jamais globalement : une erreur sur un
    site (page introuvable, CSV cassé...) est affichée puis le script
    passe au site suivant, avec un récapitulatif final."""
    import random

    if not sites:
        print(_t("tt_aucun_site", langue))
        return []

    if not avec_ia and not ia_mock and not ia_openai:
        ia_mock = True
        print(_t("tt_ia_mock_defaut", langue))

    resultats = []
    for cle, infos in sites.items():
        infos = infos or {}
        nom = infos.get("nom", cle)
        csv_url = infos.get("discover_csv")

        print(f"\n{'=' * 70}")
        print(f"  {nom} ({cle})")
        print(f"{'=' * 70}")

        if not csv_url:
            print(_t("tt_pas_de_csv", langue))
            resultats.append((nom, "ignore", None))
            continue

        try:
            lignes = charger_discover_csv(csv_url)
        except Exception as e:
            print(_t("tt_csv_erreur", langue, e=e))
            resultats.append((nom, "erreur_csv", str(e)))
            continue

        candidats = [l for l in lignes if l.get("url")]
        if not candidats:
            print(_t("tt_csv_vide", langue))
            resultats.append((nom, "ignore", None))
            continue

        ligne = random.choice(candidats)
        url = ligne["url"]
        print(_t("tt_url_choisie", langue, url=url))

        try:
            html = fetch_html(url)
        except Exception as e:
            print(_t("tt_fetch_erreur", langue, e=e))
            resultats.append((nom, "erreur_fetch", str(e)))
            continue

        try:
            rapport = analyser(url, html, avec_ia=avec_ia, ia_mock=ia_mock, ia_openai=ia_openai, avec_reality_check=True,
                                discover_csv_url=csv_url, discover_jours=discover_jours, langue=langue)
            afficher(rapport, langue)
            resultats.append((nom, "ok", None))
        except Exception as e:
            print(_t("tt_analyse_erreur", langue, e=e))
            resultats.append((nom, "erreur_analyse", str(e)))

    print(f"\n{'=' * 70}")
    print(f"  {_t('tt_recap_titre', langue)}")
    print(f"{'=' * 70}")
    icones = {"ok": "✅", "ignore": "⏭️ "}
    for nom, statut, message in resultats:
        icone = icones.get(statut, "❌")
        suffixe = f" — {message}" if message else ""
        print(f"  {icone} {nom}{suffixe}")
    print()

    return resultats


# ----------------------------------------------------------------------
# 8. CLI
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Vérificateur de qualité d'article")
    parser.add_argument("url", nargs="?", help="URL de l'article à analyser")
    parser.add_argument("--fake", choices=["good", "bad"], help="Utiliser un article fake pour tester la mécanique")
    parser.add_argument("--titre", help="Mode idée : teste un titre d'article envisagé (pas encore publié/pas d'URL). Identifie mot-clé/entités et simule le potentiel Discover réel dessus. Nécessite --ia ou --ia-mock.")
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute (en plus de l'affichage)")
    parser.add_argument("--debug", action="store_true", help="Liste le détail des hyperliens comptés dans le corps du texte")
    parser.add_argument("--ia", action="store_true", help="Ajoute un score Search (SEO) et Discover via l'API Claude (nécessite ANTHROPIC_API_KEY, appel payant)")
    parser.add_argument("--ia-mock", action="store_true", help="Comme --ia mais SANS appeler l'API : résultat simulé, pour tester la mécanique/l'affichage sans clé ni coût (en attendant la validation du compte API)")
    parser.add_argument("--ia-openai", action="store_true", help="Comme --ia mais via l'API OpenAI (gpt-4o) au lieu de Claude. Nécessite OPENAI_API_KEY.")
    parser.add_argument("--reality-check", action="store_true", help="Croise les entités identifiées (joueur/club/entraîneur) avec les vraies données Discover (nécessite --ia ou --ia-mock, et un CSV Discover : détecté auto par domaine, --site, --discover-csv, ou DISCOVER_CSV_URL)")
    parser.add_argument("--discover-csv", help="URL du CSV Discover publié (prioritaire sur --site, la détection auto par domaine, et la variable d'environnement DISCOVER_CSV_URL)")
    parser.add_argument("--site", help="Clé du site à utiliser pour le CSV Discover (voir --list-sites pour la liste) — utile en mode --titre où il n'y a pas d'URL pour détecter le site automatiquement")
    parser.add_argument("--list-sites", action="store_true", help="Affiche la liste des sites configurés dans sites_discover.json et quitte")
    parser.add_argument("--test-tous-sites", action="store_true", help="Teste une URL choisie au hasard dans le CSV Discover de CHAQUE site configuré (smoke test rapide) — pas besoin d'URL/--fake/--titre. --ia-mock utilisé par défaut si ni --ia ni --ia-mock n'est précisé.")
    parser.add_argument("--discover-jours", type=int, default=int(os.environ.get("DISCOVER_JOURS", DISCOVER_JOURS_DEFAUT)), help=f"Nombre de jours couverts par l'export Discover, pour l'affichage uniquement (défaut : {DISCOVER_JOURS_DEFAUT}, ou variable d'environnement DISCOVER_JOURS)")
    parser.add_argument("--langue", choices=list(LANGUES_DISPONIBLES.keys()), help="Langue d'affichage du rapport (fr/en/pt-br). Sans cet argument : variable d'environnement LANGUE_AFFICHAGE, sinon menu interactif au lancement, sinon fr. Le titre et les mots-clés/entités de l'article restent toujours dans leur langue d'origine, jamais traduits.")
    args = parser.parse_args()

    sites = charger_sites_config()

    if args.list_sites:
        afficher_menu_sites(sites)
        return

    nb_ia_flags = sum([args.ia, args.ia_mock, args.ia_openai])
    if nb_ia_flags > 1:
        print("Utilise UN SEUL flag IA parmi --ia, --ia-mock et --ia-openai.")
        sys.exit(1)

    if args.titre and (args.fake or args.url):
        print("Utilise --titre seul (pas en même temps qu'une URL ou --fake) : c'est un mode alternatif pour tester une idée d'article pas encore publiée.")
        sys.exit(1)

    if args.test_tous_sites and (args.titre or args.fake or args.url):
        print("Utilise --test-tous-sites seul (pas en même temps qu'une URL, --fake ou --titre) : il teste automatiquement une URL par site.")
        sys.exit(1)

    if not (args.titre or args.fake or args.url or args.test_tous_sites):
        parser.print_help()
        sys.exit(1)

    # Menu de langue interactif seulement quand on va vraiment produire un
    # rapport (pas avant --list-sites, ni avant d'afficher l'aide ci-dessus).
    langue = resoudre_langue(args.langue)

    if args.test_tous_sites:
        run_test_tous_sites(sites, avec_ia=args.ia, ia_mock=args.ia_mock, ia_openai=args.ia_openai, discover_jours=args.discover_jours, langue=langue)
        return

    if args.titre:
        discover_csv_url, _site_utilise = resoudre_discover_csv_url(sites, discover_csv=args.discover_csv, site=args.site)
        resultat = run_idee(args.titre, avec_ia=args.ia, ia_mock=args.ia_mock, ia_openai=args.ia_openai, discover_csv_url=discover_csv_url, discover_jours=args.discover_jours, langue=langue)

        if args.debug:
            print("(--debug ignoré en mode --titre : pas d'article/de liens à analyser, juste un titre.)\n")

        if args.json:
            print(json.dumps(resultat, ensure_ascii=False, indent=2))

        return

    if args.fake:
        discover_csv_url = None
        if args.reality_check:
            discover_csv_url, _site_utilise = resoudre_discover_csv_url(sites, discover_csv=args.discover_csv, site=args.site)
        rapport = run_fake(args.fake, avec_ia=args.ia, ia_mock=args.ia_mock, ia_openai=args.ia_openai, avec_reality_check=args.reality_check, discover_csv_url=discover_csv_url, discover_jours=args.discover_jours, langue=langue)
    elif args.url:
        discover_csv_url = None
        if args.reality_check:
            discover_csv_url, _site_utilise = resoudre_discover_csv_url(sites, discover_csv=args.discover_csv, site=args.site, url_article=args.url)
        rapport = run_url(args.url, avec_ia=args.ia, ia_mock=args.ia_mock, ia_openai=args.ia_openai, avec_reality_check=args.reality_check, discover_csv_url=discover_csv_url, discover_jours=args.discover_jours, langue=langue)

    if args.debug:
        print("--- Détail des hyperliens comptés dans le corps du texte ---")
        for l in rapport.liens_detectes:
            print(f"  • {l['texte']!r} -> {l['href']}")
        print()

    if args.json:
        print(json.dumps(rapport.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
