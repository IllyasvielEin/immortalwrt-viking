# RC01 static boot validation

The frozen identity-preserving MIBIB is included here. The selected U-Boot is
not distributed in Git or artifacts: its additional changes have not been
mapped to corresponding source. `materials.json` records its frozen identity
and non-sensitive private-input transport metadata.

CI receives the candidate only in the validation steps through the repository
Actions secrets `RC01_UBOOT_R1_01` through `RC01_UBOOT_R1_11`. The verifier joins
strictly bounded Base64 chunks, verifies the compressed identity, performs
bounded gzip decompression and checks the exact decoded identity in memory.
It never writes the candidate or its encoded contents to disk or artifacts.
Build recipes and unit tests do not receive these secrets.

For a private local file, run from the repository root:

```sh
python3 -B scripts/verify-rc01-boot-materials.py --uboot <private-candidate-file>
```

`RC01_UBOOT_FILE` is an alternative local input. Symlinks, special files and
wrong identities are rejected. Unit tests use a newly generated synthetic ELF
and board DTB; they do not require private inputs or contain candidate code.

The verifier checks the MIBIB container and full mapping against
`config/rc01/layout.json`, plus the candidate ELF and embedded board facts.
It never executes the bootloader, writes NAND or validates on-device recovery.
The 64 KiB ENV size is a reviewed static contract, not a CI measurement of
import or save. The 256 KiB MIBIB container is not a raw image of the entire
1 MiB partition; placement, old copies, NAND boundaries, ECC, selection and
power-loss recovery require separate validation. No device identity data or
automatic flashing script is included.
