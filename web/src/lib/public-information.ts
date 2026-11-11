export interface PublicInformation {
  published: boolean;
  legal_name: string;
  tax_id: string;
  address: string;
  phone: string;
  email: string;
  policy_version: string;
  privacy: string;
  terms: string;
  returns: string;
}

export const emptyPublicInformation: PublicInformation = {
  published: false, legal_name: "", tax_id: "", address: "", phone: "", email: "",
  policy_version: "", privacy: "", terms: "", returns: "",
};
