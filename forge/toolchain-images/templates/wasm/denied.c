#include <stdio.h>
int main(){FILE *f=fopen("/etc/passwd","r");if(f){fclose(f);return 1;}puts("denied");return 0;}
