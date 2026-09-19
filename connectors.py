"""Connecteurs officiels, jamais de scraping implicite."""
import json
import urllib.parse
import urllib.request


class FranceTravailConnector:
    """Recherche explicite dans l'API officielle France Travail, avec identifiants OAuth."""
    TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
    SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    SCOPE = "api_offresdemploiv2 o2dsoffre"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()

    def _token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("Identifiants API France Travail absents : connecteur non activé")
        body = urllib.parse.urlencode({"grant_type":"client_credentials","client_id":self.client_id,"client_secret":self.client_secret,"scope":self.SCOPE}).encode()
        request = urllib.request.Request(self.TOKEN_URL, data=body, headers={"Content-Type":"application/x-www-form-urlencoded","User-Agent":"CarnetEmploi42/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            token = json.load(response).get("access_token", "")
        if not token: raise RuntimeError("L'API France Travail n'a pas fourni de jeton d'accès")
        return token

    def search_loire(self, keyword="", city="", limit=20):
        limit = max(1, min(int(limit), 50))
        params = {"departement":"42", "range":f"0-{limit-1}"}
        if keyword.strip(): params["motsCles"] = keyword.strip()
        if city.strip(): params["commune"] = city.strip()
        request = urllib.request.Request(self.SEARCH_URL+"?"+urllib.parse.urlencode(params), headers={"Authorization":f"Bearer {self._token()}","Accept":"application/json","User-Agent":"CarnetEmploi42/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response: payload=json.load(response)
        return [self.normalize(item) for item in payload.get("resultats", [])]

    @staticmethod
    def normalize(item):
        company=item.get("entreprise") or {}; location=item.get("lieuTravail") or {}; origin=item.get("origineOffre") or {}
        return {"reference":item.get("id", ""),"source":"France Travail","title":item.get("intitule", ""),"company":company.get("nom") or "Entreprise non renseignée","city":location.get("libelle", ""),"contract":item.get("typeContratLibelle") or item.get("typeContrat", ""),"work_time":item.get("dureeTravailLibelle", ""),"description":item.get("description", ""),"published_at":str(item.get("dateCreation", ""))[:10],"source_url":origin.get("urlOrigine") or f"https://candidat.francetravail.fr/offres/recherche/detail/{item.get('id','')}"}

class SireneConnector:
    BASE = "https://api.insee.fr/api-sirene/3.11/siret"
    terms_url = "https://api.insee.fr/catalogue/"
    robots_url = "https://api.insee.fr/robots.txt"

    def __init__(self, token: str): self.token = token.strip()

    def search_loire(self, query="", workforce="", limit=50):
        if not self.token: raise RuntimeError("Clé API INSEE absente : connecteur non activé")
        limit=max(1,min(int(limit),200)); clauses=["codePostalEtablissement:42*", "etatAdministratifEtablissement:A"]
        if query: clauses.append(f'denominationUniteLegale:"{query.replace(chr(34), "")}"')
        if workforce: clauses.append(f"trancheEffectifsEtablissement:{workforce}")
        url=self.BASE+"?"+urllib.parse.urlencode({"q":" AND ".join(clauses),"nombre":limit})
        req=urllib.request.Request(url,headers={"Authorization":f"Bearer {self.token}","Accept":"application/json","User-Agent":"CarnetEmploi42/1.0"})
        with urllib.request.urlopen(req,timeout=20) as res: payload=json.load(res)
        return [self.normalize(x) for x in payload.get("etablissements",[])]

    @staticmethod
    def normalize(x):
        u=x.get("uniteLegale",{}); a=x.get("adresseEtablissement",{})
        return {"siren":x.get("siren"),"siret":x.get("siret"),"company":u.get("denominationUniteLegale") or u.get("nomUniteLegale") or "Sans dénomination", "name":x.get("enseigne1Etablissement") or u.get("denominationUniteLegale") or "Établissement", "address":" ".join(str(a.get(k,"")) for k in ("numeroVoieEtablissement","typeVoieEtablissement","libelleVoieEtablissement")).strip(), "postcode":a.get("codePostalEtablissement",""), "city":a.get("libelleCommuneEtablissement",""), "workforce":x.get("trancheEffectifsEtablissement") or "Non renseigné", "active":x.get("etatAdministratifEtablissement")=="A"}
